"""Trusted caller-event wakeup interpretation for central Radar reconciliation."""

from __future__ import annotations

import json
from pathlib import Path
import re

from .trusted_reconciler import ReconcileError
from .writer_lanes import load_writer_lanes


EXPECTED_REPOSITORY = "example/ccb-core"
_ALLOWED_EVENT_NAMES = frozenset({"workflow_run", "schedule"})
_SHA40 = re.compile(r"^[0-9a-f]{40}$")


def load_caller_event(path: str | Path) -> dict[str, object]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReconcileError("CALLER_EVENT_INVALID") from exc
    if not isinstance(raw, dict):
        raise ReconcileError("CALLER_EVENT_INVALID")
    return raw


def _active_identity_for_branch(topology_path: str | Path, branch: str) -> str:
    lanes = load_writer_lanes(topology_path)
    matches = [identity for identity, lane in lanes.items() if lane.branch == branch]
    if len(matches) != 1:
        raise ReconcileError("WAKEUP_BRANCH_NOT_ACTIVE")
    return matches[0]


def requested_identities(
    *,
    event_name: str,
    event: dict[str, object],
    topology_path: str | Path,
    repository: str,
) -> tuple[tuple[str, ...], str | None]:
    """Return canonical ACTIVE identities; workflow head is observation metadata only."""
    if repository != EXPECTED_REPOSITORY:
        raise ReconcileError("UNTRUSTED_CALLER_REPOSITORY")
    if event_name not in _ALLOWED_EVENT_NAMES:
        raise ReconcileError("UNSUPPORTED_CALLER_EVENT")
    if event_name == "schedule":
        return tuple(sorted(load_writer_lanes(topology_path))), None

    workflow_run = event.get("workflow_run")
    if not isinstance(workflow_run, dict):
        raise ReconcileError("WORKFLOW_RUN_EVENT_INVALID")
    head_branch = workflow_run.get("head_branch")
    head_sha = workflow_run.get("head_sha")
    if not isinstance(head_branch, str) or not head_branch:
        raise ReconcileError("WORKFLOW_RUN_BRANCH_INVALID")
    if not isinstance(head_sha, str) or _SHA40.fullmatch(head_sha) is None:
        raise ReconcileError("WORKFLOW_RUN_HEAD_INVALID")
    return (_active_identity_for_branch(topology_path, head_branch),), head_sha


__all__ = [
    "EXPECTED_REPOSITORY",
    "load_caller_event",
    "requested_identities",
]
