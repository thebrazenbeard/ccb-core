from __future__ import annotations

from typing import Any, Dict, List, Tuple

import logging
from textwrap import dedent

from .supabase_projection import ProjectionCommand
from .visual_identity import VisualAsset, VisualProfile, validate_visual_profile

logger = logging.getLogger(__name__)

VISUAL_UPSERT_SQL = dedent(
    """\
    insert into radar.identity_visuals
    (asset_id, identity_id, scope, state, repository_path, sha256, claimed_by, claim_type,
     claimed_at, source_ref, style_contract, supersedes, retired_at, review_after, is_current, is_active_candidate)
    values (%(asset_id)s, %(identity_id)s, %(scope)s, %(state)s, %(repository_path)s, %(sha256)s, %(claimed_by)s, %(claim_type)s,
     %(claimed_at)s, %(source_ref)s, %(style_contract)s, %(supersedes)s, %(retired_at)s, %(review_after)s, %(is_current)s, %(is_active_candidate)s)
    on conflict (asset_id) do update set
     identity_id = excluded.identity_id, scope = excluded.scope, state = excluded.state,
     repository_path = excluded.repository_path, sha256 = excluded.sha256, claimed_by = excluded.claimed_by,
     claim_type = excluded.claim_type, claimed_at = excluded.claimed_at, source_ref = excluded.source_ref,
     style_contract = excluded.style_contract, supersedes = excluded.supersedes, retired_at = excluded.retired_at,
     review_after = excluded.review_after, is_current = excluded.is_current,
     is_active_candidate = excluded.is_active_candidate, updated_at = now()
    """
)


def _visual_asset_row_from_validated(
    validated: VisualProfile, asset: VisualAsset
) -> Dict[str, Any]:
    """
    Build the DB parameter dict for a single asset using a already-validated profile.

    This internal helper assumes `validated` has already been through
    `validate_visual_profile`.
    """
    # Use asset_id membership check instead of object identity to avoid subtle mismatches.
    asset_ids = {a.asset_id for a in validated.assets}
    if asset.asset_id not in asset_ids:
        raise ValueError(
            f"Asset (asset_id={asset.asset_id}) is not part of profile (identity_id={validated.identity_id})."
        )

    return {
        "asset_id": asset.asset_id,
        "identity_id": validated.identity_id,
        "scope": asset.scope,
        "state": asset.state,
        "repository_path": asset.path,
        "sha256": asset.sha256,
        "claimed_by": asset.claimed_by,
        "claim_type": asset.claim_type,
        "claimed_at": asset.claimed_at,
        "source_ref": asset.source_ref,
        "style_contract": validated.style_contract,
        "supersedes": asset.supersedes,
        "retired_at": asset.retired_at,
        "review_after": asset.review_after,
        "is_current": validated.current.get(asset.scope) == asset.asset_id,
        "is_active_candidate": validated.active_candidate.get(asset.scope)
        == asset.asset_id,
    }


def visual_asset_row(profile: VisualProfile, asset: VisualAsset) -> Dict[str, Any]:
    """
    Public wrapper: validate the profile then build the DB row dict for the given asset.

    Prefer using the internal helper if you already have a validated profile to avoid
    double validation.
    """
    validated = validate_visual_profile(profile)
    return _visual_asset_row_from_validated(validated, asset)


def build_visual_projection_commands(profile: VisualProfile) -> Tuple[ProjectionCommand, ...]:
    """
    Build ProjectionCommand objects for all assets in the provided profile.

    This validates the profile once and uses the internal helper to avoid repeated validation.
    """
    validated = validate_visual_profile(profile)
    commands: List[ProjectionCommand] = [
        ProjectionCommand(
            operation="UPSERT_IDENTITY_VISUAL",
            schema="radar",
            sql=VISUAL_UPSERT_SQL,
            parameters=_visual_asset_row_from_validated(validated, asset),
        )
        for asset in validated.assets
    ]

    logger.debug(
        "Built %d projection commands for identity_id=%s",
        len(commands),
        validated.identity_id,
    )
    return tuple(commands)


__all__ = ["visual_asset_row", "build_visual_projection_commands"]
