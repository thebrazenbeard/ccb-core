"""Trusted Git/topology target derivation for Radar reconciliation.

Writer branches are treated as inert Git data. This module may inspect refs and
commit ancestry in a trusted checkout/object database, but never imports or
executes code from writer branches.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import subprocess
from typing import Protocol

from .writer_lanes import load_registered_writer_lanes, load_writer_lanes


class ReconcileError(RuntimeError):
    """Fail-closed trusted reconciliation error code."""


class AncestryDisposition(str, Enum):
    NOOP = "NOOP"
    FORWARD = "FORWARD"
    STALE_EVENT = "STALE_EVENT"
    DIVERGED_HISTORY = "DIVERGED_HISTORY"


@dataclass(frozen=True)
class ReconcileTarget:
    identity: str
    branch: str
    projected_head: str | None
    base_head: str
    observed_head: str
    commits: tuple[str, ...]
    disposition: AncestryDisposition
    bootstrap: bool = False


class GitRepository(Protocol):
    def branch_head(self, branch: str) -> str: ...
    def is_ancestor(self, ancestor: str, descendant: str) -> bool: ...
    def commits_between(self, base: str, head: str) -> tuple[str, ...]: ...


class CursorJournal(Protocol):
    def projected_head(self, identity: str, branch: str) -> str | None: ...


class LocalGitRepository:
    """Read-only Git object/ref access against one trusted local checkout."""

    def __init__(
        self,
        repo_dir: str | Path,
        *,
        use_remote_tracking_refs: bool = False,
    ) -> None:
        self.repo_dir = Path(repo_dir)
        self._branch_ref_prefix = (
            "refs/remotes/origin/" if use_remote_tracking_refs else "refs/heads/"
        )

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.repo_dir), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=check,
        )

    def branch_head(self, branch: str) -> str:
        result = self._run(
            "rev-parse",
            "--verify",
            f"{self._branch_ref_prefix}{branch}^{{commit}}",
            check=False,
        )
        if result.returncode != 0:
            raise ReconcileError("MISSING_WRITER_BRANCH")
        return result.stdout.strip()

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        result = self._run("merge-base", "--is-ancestor", ancestor, descendant, check=False)
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        raise ReconcileError("GIT_ANCESTRY_CHECK_FAILED")

    def commits_between(self, base: str, head: str) -> tuple[str, ...]:
        if base == head:
            return ()
        result = self._run("rev-list", "--reverse", f"{base}..{head}", check=False)
        if result.returncode != 0:
            raise ReconcileError("GIT_REV_LIST_FAILED")
        return tuple(line for line in result.stdout.splitlines() if line)


class TrustedReconciler:
    """Derive canonical reconciliation targets from topology + Git + cursor state."""

    def __init__(
        self,
        topology_path: str | Path,
        cutover_path: str | Path,
        git_repository: GitRepository,
        journal: CursorJournal,
    ) -> None:
        self.topology_path = Path(topology_path)
        self.cutover_path = Path(cutover_path)
        self.git = git_repository
        self.journal = journal

    def active_identities(self) -> tuple[str, ...]:
        return tuple(sorted(load_writer_lanes(self.topology_path)))

    def _cutover(self) -> dict[str, object]:
        try:
            raw = json.loads(self.cutover_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReconcileError("CUTOVER_CONTRACT_INVALID") from exc
        if not isinstance(raw, dict):
            raise ReconcileError("CUTOVER_CONTRACT_INVALID")
        return raw

    @staticmethod
    def _mapping(raw: dict[str, object], key: str) -> dict[str, object]:
        value = raw.get(key)
        if not isinstance(value, dict):
            raise ReconcileError(f"CUTOVER_{key.upper()}_INVALID")
        return value

    def _activation_anchor(self, identity: str, branch: str) -> str:
        cutover = self._cutover()
        current = self._mapping(cutover, "current_writer_lanes")
        if current.get(identity) != branch:
            raise ReconcileError("TOPOLOGY_CUTOVER_BRANCH_MISMATCH")

        planned = self._mapping(cutover, "planned_current_writer_lanes")
        successors = self._mapping(cutover, "verified_successor_heads")
        if planned.get(identity) == branch:
            anchor = successors.get(identity)
            if isinstance(anchor, str) and anchor:
                return anchor

        historical = self._mapping(cutover, "historical_writer_lanes")
        record = historical.get(identity)
        if isinstance(record, dict) and record.get("branch") == branch:
            anchor = record.get("head_sha")
            if isinstance(anchor, str) and anchor:
                return anchor

        raise ReconcileError("ACTIVATION_ANCHOR_MISSING")

    def derive_target(self, identity: str) -> ReconcileTarget:
        if not isinstance(identity, str) or not identity:
            raise ReconcileError("IDENTITY_REQUIRED")

        active = load_writer_lanes(self.topology_path)
        lane = active.get(identity)
        if lane is None:
            registered = load_registered_writer_lanes(self.topology_path)
            if identity in registered:
                raise ReconcileError("NON_ACTIVE_WRITER_LANE")
            raise ReconcileError("UNKNOWN_WRITER_IDENTITY")

        observed = self.git.branch_head(lane.branch)
        anchor = self._activation_anchor(identity, lane.branch)
        projected = self.journal.projected_head(identity, lane.branch)

        if not self.git.is_ancestor(anchor, observed):
            return ReconcileTarget(
                identity, lane.branch, projected, projected or anchor, observed, (),
                AncestryDisposition.DIVERGED_HISTORY, projected is None,
            )

        if projected is None:
            return ReconcileTarget(
                identity=identity,
                branch=lane.branch,
                projected_head=None,
                base_head=anchor,
                observed_head=observed,
                commits=self.git.commits_between(anchor, observed),
                disposition=AncestryDisposition.FORWARD,
                bootstrap=True,
            )

        if projected == observed:
            return ReconcileTarget(
                identity, lane.branch, projected, projected, observed, (),
                AncestryDisposition.NOOP, False,
            )

        if not self.git.is_ancestor(anchor, projected):
            return ReconcileTarget(
                identity, lane.branch, projected, projected, observed, (),
                AncestryDisposition.DIVERGED_HISTORY, False,
            )

        if self.git.is_ancestor(projected, observed):
            return ReconcileTarget(
                identity=identity,
                branch=lane.branch,
                projected_head=projected,
                base_head=projected,
                observed_head=observed,
                commits=self.git.commits_between(projected, observed),
                disposition=AncestryDisposition.FORWARD,
                bootstrap=False,
            )

        if self.git.is_ancestor(observed, projected):
            return ReconcileTarget(
                identity, lane.branch, projected, projected, observed, (),
                AncestryDisposition.STALE_EVENT, False,
            )

        return ReconcileTarget(
            identity, lane.branch, projected, projected, observed, (),
            AncestryDisposition.DIVERGED_HISTORY, False,
        )


__all__ = [
    "AncestryDisposition",
    "LocalGitRepository",
    "ReconcileError",
    "ReconcileTarget",
    "TrustedReconciler",
]
