"""Compare durable GitHub state with rebuildable provider projections."""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

SAFE_PROJECTION_REPAIR = "SAFE_PROJECTION_REPAIR"
AMBIGUOUS_DURABLE_CONFLICT = "AMBIGUOUS_DURABLE_CONFLICT"
OBSERVATION_ONLY = "OBSERVATION_ONLY"


@dataclass(frozen=True)
class Finding:
    """Represents a discrepancy between GitHub and provider state."""

    code: str
    repair_class: str
    detail: str


class Reconciler:
    """Compare durable GitHub state against rebuildable provider projections."""

    def compare(
        self,
        github_state: dict[str, object] | None,
        provider_state: dict[str, object] | None,
    ) -> tuple[Finding, ...]:
        findings: list[Finding] = []
        github_state = {} if github_state is None else github_state
        provider_state = {} if provider_state is None else provider_state

        self._reconcile_protocol_head(github_state, provider_state, findings)
        self._reconcile_lanes(github_state, provider_state, findings)
        return tuple(findings)

    def _reconcile_protocol_head(
        self,
        github_state: dict[str, object],
        provider_state: dict[str, object],
        findings: list[Finding],
    ) -> None:
        github_protocol = github_state.get("protocol_head")
        provider_protocol = provider_state.get("protocol_head")
        if github_protocol != provider_protocol:
            finding = Finding(
                "PROTOCOL_HEAD_DRIFT",
                SAFE_PROJECTION_REPAIR,
                f"provider={provider_protocol!r} github={github_protocol!r}",
            )
            findings.append(finding)
            logger.debug("Protocol drift detected: %s", finding.detail)

    def _lane_mapping(self, state: dict[str, object], side: str, findings: list[Finding]) -> dict[object, object]:
        raw = state.get("lanes", {}) or {}
        if not isinstance(raw, dict):
            findings.append(
                Finding(
                    f"INVALID_{side.upper()}_LANES",
                    OBSERVATION_ONLY if side == "provider" else AMBIGUOUS_DURABLE_CONFLICT,
                    f"{side}_lanes={raw!r}",
                )
            )
            return {}
        return dict(raw)

    def _reconcile_lanes(
        self,
        github_state: dict[str, object],
        provider_state: dict[str, object],
        findings: list[Finding],
    ) -> None:
        github_lanes = self._lane_mapping(github_state, "github", findings)
        provider_lanes = self._lane_mapping(provider_state, "provider", findings)

        # Identity keys are expected to be strings. Sorting by repr keeps
        # diagnostics deterministic even when malformed provider data arrives.
        for identity in sorted(github_lanes, key=repr):
            github_lane = github_lanes[identity]
            provider_lane = provider_lanes.get(identity)
            identity_text = str(identity)
            if provider_lane is None:
                finding = Finding(
                    "MISSING_PROVIDER_LANE",
                    SAFE_PROJECTION_REPAIR,
                    f"{identity_text}:{github_lane}",
                )
                findings.append(finding)
                logger.debug("Missing lane: %s", finding.detail)
                continue

            finding = self._reconcile_lane(identity_text, github_lane, provider_lane)
            if finding:
                findings.append(finding)
                logger.debug("Lane drift: %s", finding.detail)

        github_keys = set(github_lanes)
        for identity in sorted((key for key in provider_lanes if key not in github_keys), key=repr):
            provider_lane = provider_lanes[identity]
            finding = self._handle_orphaned_lane(str(identity), provider_lane)
            findings.append(finding)
            logger.debug("Orphaned lane: %s", finding.detail)

    @staticmethod
    def _is_multi_valued_lane(lane: object) -> bool:
        return isinstance(lane, (list, tuple, set))

    def _get_unique_claims(self, lane: object) -> tuple[object, ...]:
        """Return deterministic unique claims without requiring hashability."""
        if not self._is_multi_valued_lane(lane):
            return (lane,)

        values = list(lane)  # type: ignore[arg-type]
        unique: list[object] = []
        for value in values:
            if not any(value == existing for existing in unique):
                unique.append(value)
        return tuple(sorted(unique, key=repr))

    def _reconcile_lane(
        self,
        identity: str,
        github_lane: object,
        provider_lane: object,
    ) -> Finding | None:
        if self._is_multi_valued_lane(provider_lane):
            unique_claims = self._get_unique_claims(provider_lane)
            if len(unique_claims) > 1:
                return Finding(
                    "MULTIPLE_WRITER_CLAIMS",
                    AMBIGUOUS_DURABLE_CONFLICT,
                    f"{identity}:{unique_claims!r}",
                )
            if not unique_claims:
                provider_lane = ()
            else:
                provider_lane = unique_claims[0]

        if provider_lane != github_lane:
            return Finding(
                "LANE_PROJECTION_DRIFT",
                SAFE_PROJECTION_REPAIR,
                f"{identity}:provider={provider_lane!r} github={github_lane!r}",
            )
        return None

    def _handle_orphaned_lane(self, identity: str, provider_lane: object) -> Finding:
        if self._is_multi_valued_lane(provider_lane):
            unique_claims = self._get_unique_claims(provider_lane)
            if len(unique_claims) > 1:
                return Finding(
                    "MULTIPLE_WRITER_CLAIMS",
                    AMBIGUOUS_DURABLE_CONFLICT,
                    f"{identity}:{unique_claims!r}",
                )

        return Finding(
            "ORPHANED_PROVIDER_LANE",
            SAFE_PROJECTION_REPAIR,
            f"{identity}:{provider_lane!r}",
        )


__all__ = [
    "AMBIGUOUS_DURABLE_CONFLICT",
    "Finding",
    "OBSERVATION_ONLY",
    "Reconciler",
    "SAFE_PROJECTION_REPAIR",
]
