"""Reference model for Radar's fenced projection journal contract.

The durable implementation lives in Supabase SQL. This in-process model exists so
claim/finalization semantics can be tested without provider effects and so trusted
reconciliation code has one explicit state contract to target.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import threading
import uuid


class JournalError(RuntimeError):
    """Fail-closed projection journal error code."""


class BatchFileState(str, Enum):
    PENDING = "PENDING"
    PROJECTED = "PROJECTED"
    IDEMPOTENT = "IDEMPOTENT"
    FAILED = "FAILED"
    CONFLICT = "CONFLICT"


_TERMINAL_SUCCESS = frozenset({BatchFileState.PROJECTED, BatchFileState.IDEMPOTENT})


@dataclass(frozen=True)
class BatchClaim:
    batch_id: str
    claim_token: str
    identity: str
    branch: str
    expected_projected_head: str | None
    target_head: str
    control_sha: str
    topology_sha: str
    owner: str
    lease_expires_ms: int


@dataclass(frozen=True)
class ProjectionLaneState:
    identity: str
    branch: str
    projected_head_sha: str | None


@dataclass
class _BatchRecord:
    claim: BatchClaim
    files: dict[str, BatchFileState]
    complete: bool = False
    finalized_state: ProjectionLaneState | None = None


class ProjectionJournal:
    """Thread-safe reference implementation of the provider fencing contract."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._lanes: dict[tuple[str, str], ProjectionLaneState] = {}
        self._control_cuts: dict[tuple[str, str], tuple[str, str]] = {}
        self._batches: dict[str, _BatchRecord] = {}
        self._active_batch_by_lane: dict[tuple[str, str], str] = {}

    @staticmethod
    def _text(value: object, code: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise JournalError(code)
        return value.strip()

    @staticmethod
    def _time(value: object, code: str) -> int:
        if type(value) is not int or value < 0:
            raise JournalError(code)
        return value

    def seed_lane_state(self, identity: str, branch: str, projected_head_sha: str | None) -> None:
        """Explicitly establish a known provider cursor; claim never invents one."""
        identity = self._text(identity, "INVALID_IDENTITY")
        branch = self._text(branch, "INVALID_BRANCH")
        if projected_head_sha is not None:
            projected_head_sha = self._text(projected_head_sha, "INVALID_PROJECTED_HEAD")
        key = (identity, branch)
        with self._lock:
            existing = self._lanes.get(key)
            if existing is not None and existing.projected_head_sha != projected_head_sha:
                raise JournalError("LANE_STATE_ALREADY_SEEDED")
            self._lanes[key] = ProjectionLaneState(identity, branch, projected_head_sha)

    def projected_head(self, identity: str, branch: str) -> str | None:
        key = (identity, branch)
        with self._lock:
            state = self._lanes.get(key)
            return None if state is None else state.projected_head_sha

    def set_control_cut(
        self,
        *,
        identity: str,
        branch: str,
        control_sha: str,
        topology_sha: str,
    ) -> None:
        identity = self._text(identity, "INVALID_IDENTITY")
        branch = self._text(branch, "INVALID_BRANCH")
        control = self._text(control_sha, "INVALID_CONTROL_SHA")
        topology = self._text(topology_sha, "INVALID_TOPOLOGY_SHA")
        key = (identity, branch)
        with self._lock:
            if key not in self._lanes:
                raise JournalError("LANE_UNSEEDED")
            self._control_cuts[key] = (control, topology)

    def claim_batch(
        self,
        *,
        identity: str,
        branch: str,
        expected_projected_head: str | None,
        target_head: str,
        control_sha: str,
        topology_sha: str,
        files: tuple[str, ...],
        owner: str,
        now_ms: int,
        lease_ms: int,
    ) -> BatchClaim:
        identity = self._text(identity, "INVALID_IDENTITY")
        branch = self._text(branch, "INVALID_BRANCH")
        target = self._text(target_head, "INVALID_TARGET_HEAD")
        control = self._text(control_sha, "INVALID_CONTROL_SHA")
        topology = self._text(topology_sha, "INVALID_TOPOLOGY_SHA")
        owner = self._text(owner, "INVALID_CLAIM_OWNER")
        now = self._time(now_ms, "INVALID_CLAIM_TIME")
        lease = self._time(lease_ms, "INVALID_CLAIM_LEASE")
        if lease <= 0:
            raise JournalError("INVALID_CLAIM_LEASE")
        if expected_projected_head is not None:
            expected_projected_head = self._text(expected_projected_head, "INVALID_PROJECTED_HEAD")
        if not isinstance(files, tuple) or any(not isinstance(p, str) or not p for p in files):
            raise JournalError("INVALID_BATCH_FILES")
        if len(set(files)) != len(files):
            raise JournalError("DUPLICATE_BATCH_FILE")

        key = (identity, branch)
        with self._lock:
            current = self._lanes.get(key)
            if current is None:
                raise JournalError("LANE_UNSEEDED")
            if current.projected_head_sha != expected_projected_head:
                raise JournalError("PROJECTED_BASE_CHANGED")
            if self._control_cuts.get(key) != (control, topology):
                raise JournalError("CONTROL_CUT_CHANGED")

            previous_id = self._active_batch_by_lane.get(key)
            if previous_id is not None:
                previous = self._batches[previous_id]
                if not previous.complete and now < previous.claim.lease_expires_ms:
                    raise JournalError("CLAIM_BUSY")

            claim = BatchClaim(
                batch_id=uuid.uuid4().hex,
                claim_token=uuid.uuid4().hex,
                identity=identity,
                branch=branch,
                expected_projected_head=expected_projected_head,
                target_head=target,
                control_sha=control,
                topology_sha=topology,
                owner=owner,
                lease_expires_ms=now + lease,
            )
            self._batches[claim.batch_id] = _BatchRecord(
                claim=claim,
                files={path: BatchFileState.PENDING for path in files},
            )
            self._active_batch_by_lane[key] = claim.batch_id
            return claim

    def _record_for_current_claim(self, claim: BatchClaim) -> _BatchRecord:
        record = self._batches.get(claim.batch_id)
        if record is None or record.claim.claim_token != claim.claim_token:
            raise JournalError("STALE_CLAIM")
        key = (claim.identity, claim.branch)
        if self._active_batch_by_lane.get(key) != claim.batch_id:
            if record.complete:
                return record
            raise JournalError("STALE_CLAIM")
        return record

    def record_file_result(self, claim: BatchClaim, path: str, state: BatchFileState) -> None:
        if not isinstance(state, BatchFileState) or state is BatchFileState.PENDING:
            raise JournalError("INVALID_BATCH_FILE_STATE")
        with self._lock:
            record = self._record_for_current_claim(claim)
            if record.complete:
                raise JournalError("BATCH_ALREADY_COMPLETE")
            if path not in record.files:
                raise JournalError("UNKNOWN_BATCH_FILE")
            existing = record.files[path]
            if existing is not BatchFileState.PENDING and existing is not state:
                raise JournalError("BATCH_FILE_RESULT_CONFLICT")
            record.files[path] = state

    def finalize_batch(self, claim: BatchClaim, *, now_ms: int) -> ProjectionLaneState:
        now = self._time(now_ms, "INVALID_FINALIZE_TIME")
        with self._lock:
            record = self._batches.get(claim.batch_id)
            if record is not None and record.complete and record.claim.claim_token == claim.claim_token:
                assert record.finalized_state is not None
                return record.finalized_state

            record = self._record_for_current_claim(claim)
            if now >= claim.lease_expires_ms:
                raise JournalError("STALE_CLAIM")
            key = (claim.identity, claim.branch)
            if self._control_cuts.get(key) != (claim.control_sha, claim.topology_sha):
                raise JournalError("CONTROL_CUT_CHANGED")

            lane = self._lanes.get(key)
            if lane is None:
                raise JournalError("LANE_UNSEEDED")
            if lane.projected_head_sha != claim.expected_projected_head:
                raise JournalError("PROJECTED_BASE_CHANGED")
            if any(state not in _TERMINAL_SUCCESS for state in record.files.values()):
                raise JournalError("UNRESOLVED_BATCH_FILES")

            finalized = ProjectionLaneState(claim.identity, claim.branch, claim.target_head)
            self._lanes[key] = finalized
            record.complete = True
            record.finalized_state = finalized
            self._active_batch_by_lane.pop(key, None)
            return finalized

    def force_projected_head_for_test(self, identity: str, branch: str, head: str | None) -> None:
        """Test-only corruption hook used to prove base fencing."""
        with self._lock:
            self._lanes[(identity, branch)] = ProjectionLaneState(identity, branch, head)


__all__ = [
    "BatchClaim",
    "BatchFileState",
    "JournalError",
    "ProjectionJournal",
    "ProjectionLaneState",
]
