"""Pure GitHub-state projection helpers for Radar.

This module discovers writer lanes (branches named like `bus/<identity>-vN`) and
projects repository state into a RepositoryProjection. It also validates lane
candidates against optional evidence (messages directory presence and declared
writer information).

Example
-------
>>> project_branches(["bus/alice-v1", "bus/protocol-v1"])
RepositoryProjection(...)
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
import re
from typing import Iterable, Mapping, Optional, Sequence, Tuple, Dict

_LANE_RE = re.compile(r"^bus/(?P<identity>[a-z0-9_-]+)-v\d+$")
_ALIASES: Dict[str, str] = {
    "beta-legacy": "beta",
    "alpha-core": "alpha",
}


def _normalize_identity(value: str) -> str:
    """Normalize an identity token for comparison and aliasing."""
    key = value.strip().casefold()
    return _ALIASES.get(key, key)


@dataclass(frozen=True)
class LaneEvidence:
    """Transport-neutral structural evidence for a candidate writer lane.

    Attributes:
        has_messages_dir: True if a `messages/` directory exists for the branch.
        declared_writer: optional declared writer identity found in protocol metadata.
        message_writers: optional list/tuple of writers referenced from messages.
    """

    has_messages_dir: bool
    declared_writer: Optional[str] = None
    message_writers: Sequence[str] = ()

    def __post_init__(self) -> None:
        # Ensure message_writers is an immutable tuple internally.
        object.__setattr__(self, "message_writers", tuple(self.message_writers))

    def supports(self, identity: str) -> bool:
        """Return True when the evidence ties this lane to `identity`."""
        expected = _normalize_identity(identity)
        if self.declared_writer and _normalize_identity(self.declared_writer) == expected:
            return True
        return any(_normalize_identity(writer) == expected for writer in self.message_writers)


@dataclass(frozen=True)
class RepositoryProjection:
    """Result of projecting repository branch names and optional evidence.

    Attributes:
        observed_branches: sorted tuple of distinct branch names observed.
        identity_lanes: mapping from normalized identity -> branch name (immutable).
        missing_required: sorted tuple of normalized required identities that are missing.
        protocol_branches: sorted tuple of branches that look like protocol branches.
        malformed_lanes: sorted tuple of branches that matched lane name pattern but failed evidence checks.
    """

    observed_branches: Tuple[str, ...]
    identity_lanes: Mapping[str, str]
    missing_required: Tuple[str, ...]
    protocol_branches: Tuple[str, ...]
    malformed_lanes: Tuple[str, ...] = ()


def project_branches(
    branch_names: Iterable[str],
    *,
    required_identities: Optional[Iterable[str]] = None,
    lane_evidence: Optional[Mapping[str, LaneEvidence]] = None,
) -> RepositoryProjection:
    """Project writer-lane candidates, optionally validating mailbox evidence.

    Without ``lane_evidence`` this remains a discovery-only compatibility path.
    When evidence is supplied, every candidate writer lane must have a real
    messages directory and evidence tying the lane to its expected logical
    writer. Invalid/unverified candidates are surfaced as malformed and are not
    counted as healthy lanes.

    Args:
        branch_names: iterable of branch names (may contain duplicates).
        required_identities: identities that must be present; can be any iterable.
        lane_evidence: optional mapping from branch name -> LaneEvidence.

    Returns:
        RepositoryProjection describing the observed and validated projection.
    """
    observed = tuple(sorted(set(branch_names)))
    lanes: Dict[str, str] = {}
    protocols: list[str] = []
    malformed: list[str] = []

    for branch in observed:
        if branch.startswith("bus/protocol-v"):
            protocols.append(branch)
            continue

        match = _LANE_RE.match(branch)
        if not match:
            continue

        raw_identity = match.group("identity")
        identity = _normalize_identity(raw_identity)

        if lane_evidence is not None:
            evidence = lane_evidence.get(branch)
            if evidence is None or not evidence.has_messages_dir or not evidence.supports(identity):
                malformed.append(branch)
                continue

        existing = lanes.get(identity)
        if existing is None:
            # Because `observed` is sorted, the first branch we encounter for a
            # given identity is deterministic. Keep that branch as authority.
            lanes[identity] = branch
        elif existing != branch:
            # Preserve the first deterministic branch in the projection. We keep
            # `existing` (the first encountered one).
            lanes[identity] = existing

    required: set[str] = set()
    if required_identities is not None:
        required = {_normalize_identity(identity) for identity in required_identities}

    missing = tuple(sorted(required - set(lanes)))
    return RepositoryProjection(
        observed_branches=observed,
        identity_lanes=MappingProxyType(dict(lanes)),
        missing_required=missing,
        protocol_branches=tuple(sorted(protocols)),
        malformed_lanes=tuple(sorted(malformed)),
    )


__all__ = ["LaneEvidence", "RepositoryProjection", "project_branches"]
