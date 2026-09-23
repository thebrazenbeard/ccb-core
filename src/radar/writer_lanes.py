"""Topology-bound writer-lane identity, lifecycle, and validation primitives."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess

from .message_grammar import MessageGrammarError, parse_bus_document


_SHA40_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_ZERO_SHA = "0" * 40
_ALLOWED_LIFECYCLES = frozenset({"REGISTERED_INERT", "ACTIVE", "HISTORICAL", "RETIRED"})


class WriterLaneError(ValueError):
    pass


@dataclass(frozen=True)
class WriterLane:
    identity: str
    writer: str
    branch: str
    lifecycle: str = "ACTIVE"


def load_registered_writer_lanes(topology_path: str | Path) -> dict[str, WriterLane]:
    """Load every topology registration, including inert/history records.

    Legacy test/snapshot fixtures that predate the lifecycle field are treated
    as ACTIVE; the canonical current topology writes lifecycle explicitly.
    """
    raw = json.loads(Path(topology_path).read_text(encoding="utf-8"))
    required = raw.get("required_writer_lanes")
    if not isinstance(required, dict) or not required:
        raise WriterLaneError("WRITER_LANE_REGISTRY_MISSING")

    lanes: dict[str, WriterLane] = {}
    seen_branches: set[str] = set()
    for identity, record in required.items():
        if not isinstance(identity, str) or not identity or not isinstance(record, dict):
            raise WriterLaneError("WRITER_LANE_REGISTRY_INVALID")
        writer = record.get("writer")
        branch = record.get("branch")
        lifecycle = record.get("lifecycle", "ACTIVE")
        if not isinstance(writer, str) or not writer or not isinstance(branch, str) or not branch:
            raise WriterLaneError("WRITER_LANE_REGISTRY_INVALID")
        if not isinstance(lifecycle, str) or lifecycle not in _ALLOWED_LIFECYCLES:
            raise WriterLaneError("WRITER_LANE_LIFECYCLE_INVALID")
        if branch in seen_branches:
            raise WriterLaneError("DUPLICATE_WRITER_LANE_BRANCH")
        seen_branches.add(branch)
        lanes[identity] = WriterLane(
            identity=identity,
            writer=writer,
            branch=branch,
            lifecycle=lifecycle,
        )
    return lanes


def load_writer_lanes(topology_path: str | Path) -> dict[str, WriterLane]:
    """Load only ACTIVE current lanes for routing/reconciliation authority."""
    return {
        identity: lane
        for identity, lane in load_registered_writer_lanes(topology_path).items()
        if lane.lifecycle == "ACTIVE"
    }


def _identity_for_ref(ref: str, lanes: dict[str, WriterLane]) -> str:
    branch = ref.removeprefix("refs/heads/")
    matches = [lane.identity for lane in lanes.values() if lane.branch == branch]
    if len(matches) != 1:
        raise WriterLaneError("UNREGISTERED_WRITER_LANE")
    return matches[0]


def identity_for_ref(ref: str, topology_path: str | Path) -> str:
    """Resolve only current ACTIVE lanes."""
    return _identity_for_ref(ref, load_writer_lanes(topology_path))


def registered_identity_for_ref(ref: str, topology_path: str | Path) -> str:
    """Resolve any registered lane for guard/onboarding validation."""
    return _identity_for_ref(ref, load_registered_writer_lanes(topology_path))


def _load_cutover(cutover_path: str | Path) -> dict[str, object]:
    try:
        raw = json.loads(Path(cutover_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WriterLaneError("WRITER_LANE_CUTOVER_INVALID") from exc
    if not isinstance(raw, dict):
        raise WriterLaneError("WRITER_LANE_CUTOVER_INVALID")
    return raw


def _planned_successor_map(cutover_path: str | Path) -> dict[str, str]:
    raw = _load_cutover(cutover_path)
    planned = raw.get("planned_current_writer_lanes")
    if not isinstance(planned, dict) or not planned:
        raise WriterLaneError("WRITER_LANE_CUTOVER_PLANNED_MISSING")

    result: dict[str, str] = {}
    seen: set[str] = set()
    for identity, branch in planned.items():
        if not isinstance(identity, str) or not identity or not isinstance(branch, str) or not branch:
            raise WriterLaneError("WRITER_LANE_CUTOVER_PLANNED_INVALID")
        if branch in seen:
            raise WriterLaneError("DUPLICATE_PLANNED_WRITER_LANE_BRANCH")
        seen.add(branch)
        result[identity] = branch
    return result


def planned_successor_bootstrap_identity(
    ref: str,
    head: str,
    topology_path: str | Path,
    *,
    base: str | None,
    cutover_path: str | Path | None,
    bootstrap_main: str | None,
) -> str | None:
    if base != _ZERO_SHA or cutover_path is None:
        return None

    branch = ref.removeprefix("refs/heads/")
    planned = _planned_successor_map(cutover_path)
    matches = [identity for identity, candidate in planned.items() if candidate == branch]
    if not matches:
        return None
    if len(matches) != 1:
        raise WriterLaneError("DUPLICATE_PLANNED_WRITER_LANE_BRANCH")

    identity = matches[0]
    lanes = load_registered_writer_lanes(topology_path)
    if identity not in lanes:
        raise WriterLaneError("WRITER_LANE_CUTOVER_IDENTITY_UNKNOWN")
    if lanes[identity].branch == branch:
        return None
    if not bootstrap_main:
        raise WriterLaneError("WRITER_LANE_BOOTSTRAP_MAIN_REQUIRED")
    if head != bootstrap_main:
        raise WriterLaneError("WRITER_LANE_BOOTSTRAP_MAIN_MISMATCH")
    return identity


def _git(repo_dir: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo_dir), *args],
            text=True,
            stderr=subprocess.PIPE,
        ).strip()
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() if exc.stderr else str(exc)
        raise WriterLaneError(f"GIT_COMMAND_FAILED:{detail}") from exc


def _git_bytes(repo_dir: Path, *args: str) -> bytes:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo_dir), *args],
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        detail = (
            exc.stderr.decode("utf-8", errors="replace").strip()
            if exc.stderr
            else str(exc)
        )
        raise WriterLaneError(f"GIT_COMMAND_FAILED:{detail}") from exc


def _message_headers(
    content: bytes,
    path: str,
    *,
    historical: bool = False,
) -> dict[str, str]:
    try:
        parsed = parse_bus_document(
            content,
            path=path,
            timestamp_policy="source_fallback" if historical else "strict",
        )
    except MessageGrammarError as exc:
        code = str(exc)
        if code == "WRITER_REQUIRED":
            raise WriterLaneError(f"WRITER_LANE_WRITER_REQUIRED:{path}") from exc
        if code == "MESSAGE_ID_REQUIRED":
            raise WriterLaneError(f"WRITER_LANE_MESSAGE_ID_REQUIRED:{path}") from exc
        raise WriterLaneError(f"WRITER_LANE_MESSAGE_GRAMMAR:{path}:{code}") from exc
    return dict(parsed.headers)


def _assert_ancestor(repo_dir: Path, base: str, head: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "merge-base", "--is-ancestor", base, head],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise WriterLaneError("WRITER_LANE_BASE_NOT_ANCESTOR")


def _commits_to_validate(repo_dir: Path, head: str, base: str | None) -> list[str]:
    if not base or base == _ZERO_SHA:
        try:
            base = _git(repo_dir, "rev-parse", f"{head}^")
        except WriterLaneError as exc:
            raise WriterLaneError("WRITER_LANE_PARENT_REQUIRED") from exc
    _assert_ancestor(repo_dir, base, head)
    commits = [
        line
        for line in _git(repo_dir, "rev-list", "--reverse", f"{base}..{head}").splitlines()
        if line
    ]
    if not commits:
        raise WriterLaneError("WRITER_LANE_NO_NEW_COMMITS")
    return commits


def _message_ids_at_commit(repo_dir: Path, commit: str) -> set[str]:
    paths = [
        path
        for path in _git(
            repo_dir,
            "ls-tree",
            "-r",
            "--name-only",
            commit,
            "--",
            "messages",
        ).splitlines()
        if path.startswith("messages/") and path.endswith(".md")
    ]
    ids: set[str] = set()
    for path in paths:
        content = _git_bytes(repo_dir, "show", f"{commit}:{path}")
        message_id = _message_headers(content, path, historical=True).get("message_id", "")
        if not message_id:
            raise WriterLaneError(f"WRITER_LANE_MESSAGE_ID_REQUIRED:{path}")
        ids.add(message_id.casefold())
    return ids


def _historical_message_ids(
    repo_dir: Path,
    cutover_path: str | Path | None,
    identity: str,
) -> set[str]:
    if cutover_path is None:
        return set()

    raw = _load_cutover(cutover_path)
    historical = raw.get("historical_writer_lanes")
    if historical is None:
        return set()
    if not isinstance(historical, dict):
        raise WriterLaneError("WRITER_LANE_CUTOVER_HISTORICAL_INVALID")

    record = historical.get(identity)
    if record is None:
        return set()
    if not isinstance(record, dict):
        raise WriterLaneError("WRITER_LANE_CUTOVER_HISTORICAL_INVALID")

    branch = record.get("branch")
    head_sha = record.get("head_sha")
    if (
        not isinstance(branch, str)
        or not branch
        or not isinstance(head_sha, str)
        or not _SHA40_RE.fullmatch(head_sha)
    ):
        raise WriterLaneError("WRITER_LANE_CUTOVER_HISTORICAL_INVALID")

    return _message_ids_at_commit(repo_dir, head_sha.lower())


def _validate_commit_paths(
    repo_dir: Path,
    commit: str,
    lane: WriterLane,
    *,
    reserved_message_ids: set[str] | None = None,
) -> None:
    parent_line = _git(repo_dir, "rev-list", "--parents", "-n", "1", commit).split()
    if len(parent_line) != 2:
        raise WriterLaneError("WRITER_LANE_MERGE_COMMIT_FORBIDDEN")
    parent = parent_line[1]
    seen_message_ids = set(reserved_message_ids or ())
    seen_message_ids.update(_message_ids_at_commit(repo_dir, parent))

    raw = _git_bytes(
        repo_dir,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "-r",
        "-z",
        parent,
        commit,
    )
    fields = [value.decode("utf-8") for value in raw.split(b"\0") if value]
    if len(fields) % 2 != 0:
        raise WriterLaneError("WRITER_LANE_DIFF_INVALID")

    for index in range(0, len(fields), 2):
        status, path = fields[index : index + 2]
        if not path.startswith("messages/") or not path.endswith(".md"):
            raise WriterLaneError(f"WRITER_LANE_PATH_FORBIDDEN:{path}")
        if status != "A":
            raise WriterLaneError(f"WRITER_LANE_MESSAGE_NOT_APPEND_ONLY:{path}")

        content = _git_bytes(repo_dir, "show", f"{commit}:{path}")
        headers = _message_headers(content, path)
        declared_writer = headers.get("writer", "")
        if not declared_writer:
            raise WriterLaneError(f"WRITER_LANE_WRITER_REQUIRED:{path}")
        if declared_writer.casefold() != lane.writer.casefold():
            raise WriterLaneError(
                f"WRITER_LANE_WRITER_MISMATCH:{path}:{declared_writer}:{lane.writer}"
            )

        message_id = headers.get("message_id", "")
        if not message_id:
            raise WriterLaneError(f"WRITER_LANE_MESSAGE_ID_REQUIRED:{path}")
        if not message_id.casefold().startswith(f"{lane.identity.casefold()}-"):
            raise WriterLaneError(
                f"WRITER_LANE_MESSAGE_ID_NAMESPACE:{path}:{message_id}:{lane.identity}"
            )
        folded_message_id = message_id.casefold()
        if folded_message_id in seen_message_ids:
            raise WriterLaneError(f"DUPLICATE_NEW_MESSAGE_ID:{message_id}:{path}")
        seen_message_ids.add(folded_message_id)


def validate_writer_lane_commit(
    repo_dir: str | Path,
    ref: str,
    head: str,
    topology_path: str | Path,
    *,
    base: str | None = None,
    cutover_path: str | Path | None = None,
    bootstrap_main: str | None = None,
) -> str:
    registered_lanes = load_registered_writer_lanes(topology_path)
    repo = Path(repo_dir)
    if _git(repo, "rev-parse", "HEAD") != head:
        raise WriterLaneError("BRANCH_HEAD_MISMATCH")

    bootstrap_identity = planned_successor_bootstrap_identity(
        ref,
        head,
        topology_path,
        base=base,
        cutover_path=cutover_path,
        bootstrap_main=bootstrap_main,
    )
    if bootstrap_identity is not None:
        return bootstrap_identity

    identity = registered_identity_for_ref(ref, topology_path)
    lane = registered_lanes[identity]
    if lane.lifecycle not in {"ACTIVE", "REGISTERED_INERT"}:
        raise WriterLaneError("WRITER_LANE_NOT_WRITABLE")
    historical_ids = _historical_message_ids(repo, cutover_path, identity)
    for commit in _commits_to_validate(repo, head, base):
        _validate_commit_paths(
            repo,
            commit,
            lane,
            reserved_message_ids=historical_ids,
        )

    return identity


__all__ = [
    "WriterLane",
    "WriterLaneError",
    "identity_for_ref",
    "registered_identity_for_ref",
    "load_registered_writer_lanes",
    "load_writer_lanes",
    "planned_successor_bootstrap_identity",
    "validate_writer_lane_commit",
]
