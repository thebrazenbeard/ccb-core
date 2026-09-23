"""Trusted object-only writer-lane validation for central reconciliation.

This module intentionally reuses the writer-lane guard's existing parser/path/message-id
primitives while removing only the working-tree `HEAD == writer head` assumption.
Writer branches remain inert Git objects; no branch tree is checked out or executed.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

from .writer_lanes import (
    WriterLaneError,
    _assert_ancestor,
    _historical_message_ids,
    _validate_commit_paths,
    load_writer_lanes,
)


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


def validate_active_writer_range(
    repo_dir: str | Path,
    *,
    identity: str,
    base: str,
    head: str,
    topology_path: str | Path,
    cutover_path: str | Path | None,
) -> tuple[str, ...]:
    """Validate every unseen commit for one ACTIVE writer lane from Git objects.

    `base == head` is a valid zero-commit bootstrap/no-op range. Any forward range
    must be ancestral and every commit must satisfy the same append-only/message
    invariants used by the writer-lane guard.
    """

    repo = Path(repo_dir)
    lanes = load_writer_lanes(topology_path)
    lane = lanes.get(identity)
    if lane is None:
        raise WriterLaneError("WRITER_LANE_NOT_ACTIVE")
    if base == head:
        return ()

    _assert_ancestor(repo, base, head)
    commits = tuple(
        line
        for line in _git(repo, "rev-list", "--reverse", f"{base}..{head}").splitlines()
        if line
    )
    if not commits:
        raise WriterLaneError("WRITER_LANE_NO_NEW_COMMITS")

    historical_ids = _historical_message_ids(repo, cutover_path, identity)
    for commit in commits:
        _validate_commit_paths(
            repo,
            commit,
            lane,
            reserved_message_ids=historical_ids,
        )
    return commits


__all__ = ["validate_active_writer_range"]
