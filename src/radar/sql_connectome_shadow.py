"""Inert identity-projection comparison for a candidate SQL Connectome reader.

The caller supplies a read-only SQL function and Git-derived expected identities.
This module does not select a provider, install a client, authenticate a target,
change any routing state, or qualify Supabase replacement. A match is evidence
about one small projection only, never a provider or cutover certification.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_MAX_IDENTITIES = 1_000
_IDENTITY_SQL = (
    "select identity_id, display_name "
    "from radar.identities order by identity_id limit %s"
)

# No consumer may treat absence of these independent proofs as parity.
_UNVERIFIED = (
    "identity_only",
    "physical_provider_identity",
    "authenticated_operator_route",
    "supabase_roles_rls_jwt",
    "realtime_reconnect_and_replay",
    "application_api_storage_functions",
    "durable_projection_recovery",
    "explicit_consumer_cutover",
)


@dataclass(frozen=True)
class ShadowIdentityReport:
    """Comparison evidence with intentionally non-promotable authority flags."""

    status: str
    reason: str
    reference_source_commit: str | None
    candidate_source_commit: str | None
    expected_digest: str | None
    observed_digest: str | None
    unverified_capabilities: tuple[str, ...] = _UNVERIFIED
    canonical: bool = False
    cutover_authorized: bool = False


def _identities(raw: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, (list, tuple)) or len(raw) > _MAX_IDENTITIES:
        raise ValueError("identity projection must be a bounded list or tuple")
    identities: dict[str, str] = {}
    for row in raw:
        if not isinstance(row, Mapping):
            raise ValueError("identity rows must be mappings")
        identity_id, display_name = row.get("identity_id"), row.get("display_name")
        if (
            not isinstance(identity_id, str)
            or not identity_id.strip()
            or not isinstance(display_name, str)
            or not display_name.strip()
        ):
            raise ValueError("identity rows require nonempty text")
        canonical_id = identity_id.strip().casefold()
        if canonical_id in identities:
            raise ValueError("duplicate normalized identity ID")
        identities[canonical_id] = display_name.strip()
    return tuple(sorted(identities.items()))


def _digest(rows: tuple[tuple[str, str], ...]) -> str:
    return hashlib.sha256(
        json.dumps(rows, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()


def compare_identity_shadow(
    expected_identities: Sequence[Mapping[str, object]],
    candidate_read: Callable[[str, tuple[int]], Any],
    *,
    reference_source_commit: str,
    candidate_source_commit: str,
) -> ShadowIdentityReport:
    """Compare one bounded `radar.identities` snapshot without any write SQL.

    ``candidate_read`` must be operator-provided and separately authenticated
    to a read-only, transactionally consistent SQL Connectome target. Its
    provenance and scope cannot be verified here. Both source commit inputs are
    caller assertions, not proof of installed source or provider identity.

    The only SQL issued here is a fixed, parameter-bound SELECT with N+1 rows
    (extra rows must not be silently ignored). Exceptions and malformed rows
    remain UNKNOWN. Even exact equality returns MATCH_UNQUALIFIED.
    """
    if (
        not isinstance(reference_source_commit, str)
        or not _SHA1.fullmatch(reference_source_commit)
        or not isinstance(candidate_source_commit, str)
        or not _SHA1.fullmatch(candidate_source_commit)
    ):
        return ShadowIdentityReport(
            "UNKNOWN", "INVALID_SOURCE_PIN", None, None, None, None
        )
    try:
        expected = _identities(expected_identities)
    except (TypeError, ValueError):
        return ShadowIdentityReport(
            "UNKNOWN", "INVALID_EXPECTED_PROJECTION", reference_source_commit,
            candidate_source_commit, None, None
        )

    expected_digest = _digest(expected)
    try:
        raw = candidate_read(_IDENTITY_SQL, (len(expected) + 1,))
    except Exception:
        return ShadowIdentityReport(
            "UNKNOWN", "READBACK_UNAVAILABLE", reference_source_commit,
            candidate_source_commit, expected_digest, None
        )
    try:
        observed = _identities(raw)
    except (TypeError, ValueError):
        return ShadowIdentityReport(
            "UNKNOWN", "INVALID_CANDIDATE_PROJECTION", reference_source_commit,
            candidate_source_commit,
            expected_digest, None,
        )

    observed_digest = _digest(observed)
    if expected != observed:
        return ShadowIdentityReport(
            "MISMATCH", "IDENTITY_ROWS_DIFFER", reference_source_commit,
            candidate_source_commit,
            expected_digest, observed_digest,
        )
    return ShadowIdentityReport(
        "MATCH_UNQUALIFIED", "ONE_PROJECTION_MATCHES_ONLY", reference_source_commit,
        candidate_source_commit,
        expected_digest, observed_digest,
    )


__all__ = ["ShadowIdentityReport", "compare_identity_shadow"]
