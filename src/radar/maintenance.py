"""Exception-oriented Radar maintenance checks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypedDict


class MaintenanceError(ValueError):
    """Malformed maintenance input that cannot be interpreted safely."""


class FindingCode(str, Enum):
    MISSING_LANE = "MISSING_LANE"
    MALFORMED_LANE = "MALFORMED_LANE"
    DLQ_PENDING = "DLQ_PENDING"
    STALE_ENDPOINT = "STALE_ENDPOINT"
    STUCK_DEPENDENCY = "STUCK_DEPENDENCY"
    STALE_ACKNOWLEDGEMENT = "STALE_ACKNOWLEDGEMENT"
    PROTOCOL_DRIFT = "PROTOCOL_DRIFT"
    PROVIDER_MISMATCH = "PROVIDER_MISMATCH"
    CI_FAILED = "CI_FAILED"
    STALE_DOCS = "STALE_DOCS"
    ORPHANED_IDENTITY = "ORPHANED_IDENTITY"


class RadarState(TypedDict, total=False):
    required_lanes: set[str]
    observed_lanes: set[str]
    malformed_lanes: tuple[str, ...]
    dlq_pending: int
    stale_endpoints: int
    stuck_dependencies: int
    stale_acknowledgements: int
    protocol_drift: bool
    provider_mismatch: bool
    ci_failed: bool
    stale_docs: bool
    orphaned_identities: tuple[str, ...]


@dataclass(frozen=True)
class MaintenanceFinding:
    code: str
    detail: str


def _strict_nonnegative_count(state: RadarState, key: str) -> int:
    raw = state.get(key, 0)  # type: ignore[literal-required]
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise MaintenanceError(f"INVALID_COUNT:{key}")
    return raw


def _strict_flag(state: RadarState, key: str) -> bool:
    raw = state.get(key, False)  # type: ignore[literal-required]
    if not isinstance(raw, bool):
        raise MaintenanceError(f"INVALID_FLAG:{key}")
    return raw


def _check_missing_lanes(findings: list[MaintenanceFinding], state: RadarState) -> None:
    try:
        required = set(state.get("required_lanes", set()) or set())
        observed = set(state.get("observed_lanes", set()) or set())
    except TypeError as exc:
        raise MaintenanceError("INVALID_LANE_COLLECTION") from exc
    for identity in sorted(required - observed, key=str):
        findings.append(MaintenanceFinding(FindingCode.MISSING_LANE.value, str(identity)))


def _check_malformed_lanes(findings: list[MaintenanceFinding], state: RadarState) -> None:
    raw = state.get("malformed_lanes", ()) or ()
    if isinstance(raw, (str, bytes)):
        raise MaintenanceError("INVALID_MALFORMED_LANES")
    try:
        malformed = tuple(sorted(raw, key=str))
    except TypeError as exc:
        raise MaintenanceError("INVALID_MALFORMED_LANES") from exc
    for branch in malformed:
        findings.append(MaintenanceFinding(FindingCode.MALFORMED_LANE.value, str(branch)))


def _check_positive_count(
    findings: list[MaintenanceFinding],
    code: FindingCode,
    state: RadarState,
    key: str,
) -> None:
    value = _strict_nonnegative_count(state, key)
    if value > 0:
        findings.append(MaintenanceFinding(code.value, str(value)))


def _check_boolean_flags(findings: list[MaintenanceFinding], state: RadarState) -> None:
    boolean_checks = (
        ("protocol_drift", FindingCode.PROTOCOL_DRIFT, "current pointer differs"),
        ("provider_mismatch", FindingCode.PROVIDER_MISMATCH, "provider projection differs"),
        ("ci_failed", FindingCode.CI_FAILED, "current integration head is red"),
        ("stale_docs", FindingCode.STALE_DOCS, "current pointers or docs are stale"),
    )
    for state_key, code, detail in boolean_checks:
        if _strict_flag(state, state_key):
            findings.append(MaintenanceFinding(code.value, detail))


def _check_orphaned_identities(findings: list[MaintenanceFinding], state: RadarState) -> None:
    raw = state.get("orphaned_identities", ()) or ()
    if isinstance(raw, (str, bytes)):
        raise MaintenanceError("INVALID_ORPHANED_IDENTITIES")
    try:
        orphaned = tuple(sorted(raw, key=str))
    except TypeError as exc:
        raise MaintenanceError("INVALID_ORPHANED_IDENTITIES") from exc
    for identity in orphaned:
        findings.append(MaintenanceFinding(FindingCode.ORPHANED_IDENTITY.value, str(identity)))


def audit(state: dict[str, object]) -> tuple[MaintenanceFinding, ...]:
    """Audit Radar state and return exception-oriented health findings.

    Malformed input is rejected explicitly rather than coerced into a plausible
    finding. This prevents values such as ``"false"`` from becoming true and
    booleans from being counted as integers.
    """
    if not isinstance(state, dict):
        raise MaintenanceError("INVALID_STATE")

    typed_state = state  # runtime validation is performed by the helpers above
    findings: list[MaintenanceFinding] = []
    _check_missing_lanes(findings, typed_state)  # type: ignore[arg-type]
    _check_malformed_lanes(findings, typed_state)  # type: ignore[arg-type]
    _check_positive_count(findings, FindingCode.DLQ_PENDING, typed_state, "dlq_pending")  # type: ignore[arg-type]
    _check_positive_count(findings, FindingCode.STALE_ENDPOINT, typed_state, "stale_endpoints")  # type: ignore[arg-type]
    _check_positive_count(findings, FindingCode.STUCK_DEPENDENCY, typed_state, "stuck_dependencies")  # type: ignore[arg-type]
    _check_positive_count(findings, FindingCode.STALE_ACKNOWLEDGEMENT, typed_state, "stale_acknowledgements")  # type: ignore[arg-type]
    _check_boolean_flags(findings, typed_state)  # type: ignore[arg-type]
    _check_orphaned_identities(findings, typed_state)  # type: ignore[arg-type]
    return tuple(findings)


__all__ = [
    "FindingCode",
    "MaintenanceError",
    "MaintenanceFinding",
    "RadarState",
    "audit",
]
