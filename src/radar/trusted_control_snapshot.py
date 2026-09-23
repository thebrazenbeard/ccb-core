"""Materialize current canonical control contracts as inert Git object data.

Trusted projector code is immutable, but topology/currentness is intentionally
mutable canonical data on GitHub main. This module reads only approved contract
blobs from refs/remotes/origin/main; it never checks out or executes that tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess


_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_MAIN_REF = "refs/remotes/origin/main"
_TOPOLOGY_REL = "architecture/contracts/RADAR_TOPOLOGY_V1.json"
_CUTOVER_REL = "architecture/contracts/RADAR_WRITER_LANE_V2_CUTOVER.json"


class ControlSnapshotError(RuntimeError):
    pass


@dataclass(frozen=True)
class CurrentControlSnapshot:
    main_head_sha: str
    topology_blob_sha: str
    cutover_blob_sha: str
    topology_path: Path
    cutover_path: Path


def _git(repo_dir: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise ControlSnapshotError("GIT_CONTROL_SNAPSHOT_FAILED")
    return result.stdout.strip()


def _git_bytes(repo_dir: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise ControlSnapshotError("CURRENT_CONTROL_CONTRACT_MISSING")
    return result.stdout


def _resolve_blob(repo_dir: Path, commit: str, relative: str) -> str:
    try:
        blob = _git(repo_dir, "rev-parse", f"{commit}:{relative}")
    except ControlSnapshotError as exc:
        raise ControlSnapshotError("CURRENT_CONTROL_CONTRACT_MISSING") from exc
    if _SHA40.fullmatch(blob) is None:
        raise ControlSnapshotError("CURRENT_CONTROL_BLOB_INVALID")
    return blob


def materialize_current_control_snapshot(
    repo_dir: str | Path,
    output_dir: str | Path,
) -> CurrentControlSnapshot:
    repo = Path(repo_dir).resolve()
    output = Path(output_dir).resolve()

    try:
        main_head = _git(repo, "rev-parse", "--verify", f"{_MAIN_REF}^{{commit}}")
    except ControlSnapshotError as exc:
        raise ControlSnapshotError("CURRENT_MAIN_REF_MISSING") from exc
    if _SHA40.fullmatch(main_head) is None:
        raise ControlSnapshotError("CURRENT_MAIN_SHA_INVALID")

    topology_blob = _resolve_blob(repo, main_head, _TOPOLOGY_REL)
    cutover_blob = _resolve_blob(repo, main_head, _CUTOVER_REL)
    topology_bytes = _git_bytes(repo, "show", f"{main_head}:{_TOPOLOGY_REL}")
    cutover_bytes = _git_bytes(repo, "show", f"{main_head}:{_CUTOVER_REL}")

    output.mkdir(parents=True, exist_ok=True)
    topology_path = output / "RADAR_TOPOLOGY_V1.json"
    cutover_path = output / "RADAR_WRITER_LANE_V2_CUTOVER.json"
    topology_path.write_bytes(topology_bytes)
    cutover_path.write_bytes(cutover_bytes)

    return CurrentControlSnapshot(
        main_head_sha=main_head,
        topology_blob_sha=topology_blob,
        cutover_blob_sha=cutover_blob,
        topology_path=topology_path,
        cutover_path=cutover_path,
    )


__all__ = [
    "ControlSnapshotError",
    "CurrentControlSnapshot",
    "materialize_current_control_snapshot",
]
