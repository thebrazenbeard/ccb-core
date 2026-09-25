#!/usr/bin/env python3
"""Project canonical ACTIVE writer-lane deltas through the fenced provider journal."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from radar.central_projection import (
    CentralProjectionError,
    ProviderProjectionClient,
    chunk_projection_files,
    collect_projection_files,
)
from radar.github_actions_oidc import request_github_actions_oidc_token
from radar.trusted_lane_validation import validate_active_writer_range
from radar.trusted_reconciler import (
    AncestryDisposition,
    LocalGitRepository,
    ReconcileError,
    TrustedReconciler,
)
from radar.trusted_wakeup import load_caller_event, requested_identities
from radar.writer_lanes import WriterLaneError, load_writer_lanes


CLAIM_LEASE_SECONDS = 15 * 60
_SHA40 = re.compile(r"^[0-9a-f]{40}$")


class _CursorJournal:
    def __init__(self, identity: str, branch: str, projected_head: str | None) -> None:
        self.identity = identity
        self.branch = branch
        self.head = projected_head

    def projected_head(self, identity: str, branch: str) -> str | None:
        if identity != self.identity or branch != self.branch:
            raise ReconcileError("CURSOR_JOURNAL_LANE_MISMATCH")
        return self.head


def _git(repo_dir: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo_dir), *args],
            text=True,
            stderr=subprocess.PIPE,
        ).strip()
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() if exc.stderr else str(exc)
        raise ReconcileError(f"GIT_COMMAND_FAILED:{detail}") from exc


def _provider_cursor(lane: dict[str, object] | None) -> str | None:
    if lane is None:
        raise CentralProjectionError("PROVIDER_LANE_UNSEEDED")
    head = lane.get("projected_head_sha")
    if head is None:
        return None
    if not isinstance(head, str) or _SHA40.fullmatch(head) is None:
        raise CentralProjectionError("PROVIDER_CURSOR_INVALID")
    return head


def _run_owner() -> str:
    run_id = os.environ.get("GITHUB_RUN_ID")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT")
    if not run_id or not attempt:
        raise CentralProjectionError("GITHUB_RUN_IDENTITY_REQUIRED")
    return f"github-actions:{run_id}:{attempt}"


def project_requested_lanes(
    *,
    repo_dir: Path,
    topology_path: Path,
    cutover_path: Path,
    event_name: str,
    event: dict[str, object],
    repository: str,
    client: ProviderProjectionClient,
    owner: str,
) -> dict[str, object]:
    identities, observed_hint = requested_identities(
        event_name=event_name,
        event=event,
        topology_path=topology_path,
        repository=repository,
    )
    lanes = load_writer_lanes(topology_path)
    git = LocalGitRepository(repo_dir, use_remote_tracking_refs=True)
    control_sha = _git(repo_dir, "rev-parse", "HEAD")
    topology_sha = _git(
        repo_dir,
        "rev-parse",
        f"HEAD:{topology_path.relative_to(repo_dir).as_posix()}",
    )
    if _SHA40.fullmatch(control_sha) is None or _SHA40.fullmatch(topology_sha) is None:
        raise ReconcileError("TRUSTED_CONTROL_CUT_INVALID")

    reports: list[dict[str, object]] = []
    for identity in identities:
        lane = lanes[identity]
        cursor = _provider_cursor(client.get_lane(identity, lane.branch))
        reconciler = TrustedReconciler(
            topology_path=topology_path,
            cutover_path=cutover_path,
            git_repository=git,
            journal=_CursorJournal(identity, lane.branch, cursor),
        )
        target = reconciler.derive_target(identity)
        report: dict[str, object] = {
            "identity": identity,
            "branch": lane.branch,
            "disposition": target.disposition.value,
            "projected_head": target.projected_head,
            "observed_head": target.observed_head,
        }
        if observed_hint is not None:
            report["workflow_run_head_hint"] = observed_hint
            report["hint_matches_observed_head"] = observed_hint == target.observed_head

        if target.disposition in {AncestryDisposition.NOOP, AncestryDisposition.STALE_EVENT}:
            reports.append(report)
            continue
        if target.disposition is AncestryDisposition.DIVERGED_HISTORY:
            raise ReconcileError(f"DIVERGED_HISTORY:{identity}:{lane.branch}")

        commits = validate_active_writer_range(
            repo_dir,
            identity=identity,
            base=target.base_head,
            head=target.observed_head,
            topology_path=topology_path,
            cutover_path=cutover_path,
        )
        if commits != target.commits:
            raise ReconcileError("TRUSTED_RANGE_MISMATCH")
        files = collect_projection_files(repo_dir, commits)
        file_paths = tuple(str(item["path"]) for item in files)

        client.set_control_cut(identity, lane.branch, control_sha, topology_sha)
        claim = client.claim_batch(
            identity=identity,
            branch=lane.branch,
            expected_projected_head=target.projected_head,
            target_head=target.observed_head,
            control_sha=control_sha,
            topology_sha=topology_sha,
            files=file_paths,
            owner=owner,
            lease_seconds=CLAIM_LEASE_SECONDS,
        )
        batch_id = str(claim["batch_id"])
        claim_token = str(claim["claim_token"])

        projected = 0
        for chunk in chunk_projection_files(files):
            rows = client.project_batch(
                identity=identity,
                branch=lane.branch,
                branch_head=target.observed_head,
                batch_id=batch_id,
                claim_token=claim_token,
                files=chunk,
            )
            for row in rows:
                status = row.get("result")
                if not isinstance(status, str) or status.casefold() in {
                    "conflict",
                    "failed",
                    "error",
                }:
                    raise CentralProjectionError("PROVIDER_FILE_RESULT_FAILED")
            projected += len(rows)

        finalized = client.finalize_batch(batch_id, claim_token)
        if finalized.get("projected_head_sha") != target.observed_head:
            raise CentralProjectionError("PROVIDER_FINAL_CURSOR_MISMATCH")
        report["projected_files"] = projected
        report["finalized_head"] = target.observed_head
        reports.append(report)

    return {
        "ok": True,
        "mode": "project",
        "repository": repository,
        "event_name": event_name,
        "lanes": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", default=str(ROOT))
    parser.add_argument(
        "--topology",
        default=str(ROOT / "architecture/contracts/RADAR_TOPOLOGY_V1.json"),
    )
    parser.add_argument(
        "--cutover",
        default=str(ROOT / "architecture/contracts/RADAR_WRITER_LANE_V2_CUTOVER.json"),
    )
    parser.add_argument("--event-path", default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME"))
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--endpoint", default=os.environ.get("RADAR_PROJECTION_ENDPOINT"))
    args = parser.parse_args()

    if os.environ.get("RADAR_RECONCILE_MODE") != "project":
        raise ReconcileError("PROJECT_MODE_REQUIRED")
    if not args.event_path or not args.event_name or not args.repository:
        raise ReconcileError("CALLER_CONTEXT_REQUIRED")
    if not args.endpoint:
        raise ReconcileError("PROJECTION_ENDPOINT_REQUIRED")

    repo_dir = Path(args.repo_dir).resolve()
    topology_path = Path(args.topology).resolve()
    cutover_path = Path(args.cutover).resolve()
    client = ProviderProjectionClient(
        args.endpoint,
        token_provider=request_github_actions_oidc_token,
    )
    report = project_requested_lanes(
        repo_dir=repo_dir,
        topology_path=topology_path,
        cutover_path=cutover_path,
        event_name=args.event_name,
        event=load_caller_event(args.event_path),
        repository=args.repository,
        client=client,
        owner=_run_owner(),
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CentralProjectionError, ReconcileError, WriterLaneError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
