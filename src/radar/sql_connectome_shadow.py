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
_MAX_FIELD_BYTES = 4_096
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


@dataclass(frozen=True, slots=True)
class ShadowIdentityReport:
    """Source-only comparison with non-constructible authority-denial flags.

    These computed properties prevent accidental promotion by ordinary
    constructors or ``dataclasses.replace``. A Python object is never itself
    an authorization credential or a trustworthy database attestation.
    """

    status: str
    reason: str
    reference_source_commit: str | None
    candidate_source_commit: str | None
    expected_digest: str | None
    observed_digest: str | None

    @property
    def unverified_capabilities(self) -> tuple[str, ...]:
        return _UNVERIFIED

    @property
    def canonical(self) -> bool:
        return False

    @property
    def cutover_authorized(self) -> bool:
        return False


def _bounded_text(value: object) -> str:
    if not isinstance(value, str) or len(value) > _MAX_FIELD_BYTES:
        raise ValueError("identity field exceeds text bound")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError("invalid identity UTF-8 text") from exc
    if len(encoded) > _MAX_FIELD_BYTES or not value.strip():
        raise ValueError("identity rows require bounded nonempty text")
    return value.strip()


def _identities(raw: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, (list, tuple)) or len(raw) > _MAX_IDENTITIES:
        raise ValueError("identity projection must be a bounded list or tuple")
    identities: dict[str, str] = {}
    for row in raw:
        if not isinstance(row, Mapping):
            raise ValueError("identity rows must be mappings")
        identity_id, display_name = row.get("identity_id"), row.get("display_name")
        canonical_id = _bounded_text(identity_id).casefold()
        if canonical_id in identities:
            raise ValueError("duplicate normalized identity ID")
        identities[canonical_id] = _bounded_text(display_name)
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
    except Exception:
        return ShadowIdentityReport(
            "UNKNOWN", "INVALID_EXPECTED_PROJECTION", reference_source_commit,
            candidate_source_commit, None, None
        )

    # An empty reference can agree with an empty target without proving that
    # the expected CCB identity projection was actually supplied. Refuse that
    # vacuous match before invoking an operator-controlled reader.
    if not expected:
        return ShadowIdentityReport(
            "UNKNOWN", "EMPTY_EXPECTED_PROJECTION", reference_source_commit,
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
    except Exception:
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
