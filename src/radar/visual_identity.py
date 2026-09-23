from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, TypeAlias


class VisualIdentityError(ValueError):
    """Raised when repository visual identity evidence is invalid."""


# Allowed values for visual asset attributes
_ALLOWED_SCOPES = frozenset({"portrait", "bus_avatar", "character"})
_ALLOWED_STATES = frozenset({"PLACEHOLDER", "CANDIDATE", "CANONICAL", "RETIRED"})
_ALLOWED_CLAIM_TYPES = frozenset({"self", "operator_override", "system_placeholder"})
_STYLE_CONTRACT_VERSION = "BUS_AVATAR_STYLE_V1"
_ASSET_BASE_PATH = PurePosixPath("identities")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Type alias for asset reference maps
ScopeMap: TypeAlias = dict[str, str]


@dataclass(frozen=True)
class VisualAsset:
    """Immutable representation of a visual asset tied to a visual identity.
    
    Attributes:
        asset_id: Unique identifier for this asset.
        scope: Asset scope (e.g., "portrait", "bus_avatar", "character").
        state: Lifecycle state (PLACEHOLDER, CANDIDATE, CANONICAL, or RETIRED).
        path: Relative path to the asset file, validated for safety.
        sha256: SHA256 hash of the asset file (hex-encoded, 64 chars).
        claimed_by: User or system that claimed this asset.
        claim_type: Type of claim (self, operator_override, or system_placeholder).
        claimed_at: ISO 8601 timestamp when the asset was claimed.
        source_ref: Git ref (branch/commit) where the asset is sourced.
        supersedes: Optional asset_id this asset replaces.
        retired_at: ISO 8601 timestamp when asset was retired (required if state==RETIRED).
        review_after: Optional ISO 8601 timestamp for scheduling review.
    """
    asset_id: str
    scope: str
    state: str
    path: str
    sha256: str
    claimed_by: str
    claim_type: str
    claimed_at: str
    source_ref: str
    supersedes: str | None = None
    retired_at: str | None = None
    review_after: str | None = None


@dataclass(frozen=True)
class VisualProfile:
    """Immutable representation of a visual identity and its associated assets.
    
    Attributes:
        identity_id: Unique identifier for the visual identity.
        display_name: Human-readable name for the identity.
        style_contract: Schema version (currently only "BUS_AVATAR_STYLE_V1").
        current: Map of scope -> canonical asset_id (assets in active use).
        active_candidate: Map of scope -> candidate asset_id (proposed replacements).
        assets: Tuple of all VisualAsset instances for this identity.
    """
    identity_id: str
    display_name: str
    style_contract: str
    current: ScopeMap
    active_candidate: ScopeMap
    assets: tuple[VisualAsset, ...]


def _required_string(record: Mapping[str, Any], key: str, context: str = "") -> str:
    """Extract and validate a required non-empty string field.
    
    Args:
        record: Dictionary to extract from.
        key: Field name to extract.
        context: Optional context string for error messages (e.g., asset identifier).
        
    Returns:
        The stripped string value.
        
    Raises:
        VisualIdentityError: If the field is missing, not a string, or empty after strip.
    """
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        msg = f"MISSING_OR_INVALID_{key.upper()}"
        if context:
            msg += f" (in {context})"
        raise VisualIdentityError(msg)
    return value


def _validate_asset_path(path: str, identity_id: str) -> PurePosixPath:
    """Validate that an asset path is safe and correctly structured.
    
    Asset paths must:
    - Be relative (not absolute)
    - Not contain directory traversal sequences (..)
    - Start with identities/{identity_id}/assets/
    - Have at least one component after the base directory
    
    Args:
        path: The path to validate.
        identity_id: The identity owning this asset.
        
    Returns:
        The validated PurePosixPath object.
        
    Raises:
        VisualIdentityError: If the path is invalid or doesn't match the expected structure.
    """
    asset_path = PurePosixPath(path)
    expected_prefix = _ASSET_BASE_PATH / identity_id / "assets"
    
    if asset_path.is_absolute() or ".." in asset_path.parts:
        raise VisualIdentityError("ASSET_PATH_IDENTITY_MISMATCH")
    
    if asset_path.parts[: len(expected_prefix.parts)] != expected_prefix.parts:
        raise VisualIdentityError("ASSET_PATH_IDENTITY_MISMATCH")
    
    if len(asset_path.parts) <= len(expected_prefix.parts):
        raise VisualIdentityError("ASSET_PATH_IDENTITY_MISMATCH")
    
    return asset_path


def _parse_asset(identity_id: str, record: Mapping[str, Any]) -> VisualAsset:
    """Parse and validate a single asset record.
    
    Args:
        identity_id: The identity this asset belongs to.
        record: Raw dictionary with asset fields.
        
    Returns:
        A validated VisualAsset instance.
        
    Raises:
        VisualIdentityError: If any field is invalid or missing.
    """
    asset_id = _required_string(record, "asset_id")
    scope = _required_string(record, "scope")
    state = _required_string(record, "state")
    path = _required_string(record, "path")
    sha256 = _required_string(record, "sha256")
    claimed_by = _required_string(record, "claimed_by")
    claim_type = _required_string(record, "claim_type")
    claimed_at = _required_string(record, "claimed_at")
    source_ref = _required_string(record, "source_ref")

    if scope not in _ALLOWED_SCOPES:
        raise VisualIdentityError("UNKNOWN_VISUAL_SCOPE")
    if state not in _ALLOWED_STATES:
        raise VisualIdentityError("UNKNOWN_VISUAL_STATE")
    if claim_type not in _ALLOWED_CLAIM_TYPES:
        raise VisualIdentityError("UNKNOWN_CLAIM_TYPE")
    if not _SHA256_RE.fullmatch(sha256):
        raise VisualIdentityError("INVALID_SHA256")

    _validate_asset_path(path, identity_id)

    retired_at = record.get("retired_at")
    if state == "RETIRED" and (not isinstance(retired_at, str) or not retired_at.strip()):
        raise VisualIdentityError("RETIRED_ASSET_MISSING_RETIRED_AT")

    return VisualAsset(
        asset_id=asset_id,
        scope=scope,
        state=state,
        path=path,
        sha256=sha256,
        claimed_by=claimed_by,
        claim_type=claim_type,
        claimed_at=claimed_at,
        source_ref=source_ref,
        supersedes=record.get("supersedes"),
        retired_at=retired_at,
        review_after=record.get("review_after"),
    )


def validate_visual_profile(data: Mapping[str, Any] | VisualProfile) -> VisualProfile:
    """Validate and construct a VisualProfile from raw data or return an existing one.
    
    If passed a VisualProfile, performs full cross-field validation.
    If passed a dict, parses and validates all fields and constraints.
    
    Validation ensures:
    - All required fields are present and valid
    - All assets are parseable and have unique IDs
    - At most one CANONICAL asset per scope
    - Entries in 'current' reference CANONICAL assets
    - Entries in 'active_candidate' reference CANDIDATE assets
    
    Args:
        data: Raw profile dictionary or an existing VisualProfile instance.
        
    Returns:
        A validated VisualProfile with all constraints satisfied.
        
    Raises:
        VisualIdentityError: If validation fails. Common errors include:
            - MISSING_OR_INVALID_*: Required field missing or malformed
            - UNKNOWN_STYLE_CONTRACT: style_contract is not "BUS_AVATAR_STYLE_V1"
            - INVALID_ASSET_LIST: assets field is not a list
            - DUPLICATE_ASSET_ID: Multiple assets share the same asset_id
            - MULTIPLE_CANONICAL_ASSETS_FOR_SCOPE: A scope has >1 CANONICAL asset
            - CURRENT_ASSET_INVALID: Reference in 'current' is invalid
            - ACTIVE_CANDIDATE_INVALID: Reference in 'active_candidate' is invalid
    """
    if isinstance(data, VisualProfile):
        profile = data
    else:
        identity_id = _required_string(data, "identity_id")
        display_name = _required_string(data, "display_name")
        style_contract = _required_string(data, "style_contract")
        if style_contract != _STYLE_CONTRACT_VERSION:
            raise VisualIdentityError("UNKNOWN_STYLE_CONTRACT")

        raw_assets = data.get("assets", [])
        if not isinstance(raw_assets, list):
            raise VisualIdentityError("INVALID_ASSET_LIST")
        assets = tuple(_parse_asset(identity_id, record) for record in raw_assets)

        raw_current = data.get("current", {})
        raw_candidate = data.get("active_candidate", {})
        if not isinstance(raw_current, dict) or not isinstance(raw_candidate, dict):
            raise VisualIdentityError("INVALID_CURRENT_POINTERS")

        profile = VisualProfile(
            identity_id=identity_id,
            display_name=display_name,
            style_contract=style_contract,
            current=dict(raw_current),
            active_candidate=dict(raw_candidate),
            assets=assets,
        )

    # Cross-field validation
    by_id: dict[str, VisualAsset] = {}
    canonical_by_scope: dict[str, list[VisualAsset]] = {}
    for asset in profile.assets:
        if asset.asset_id in by_id:
            raise VisualIdentityError("DUPLICATE_ASSET_ID")
        by_id[asset.asset_id] = asset
        if asset.state == "CANONICAL":
            canonical_by_scope.setdefault(asset.scope, []).append(asset)

    if any(len(items) > 1 for items in canonical_by_scope.values()):
        raise VisualIdentityError("MULTIPLE_CANONICAL_ASSETS_FOR_SCOPE")

    for scope, asset_id in profile.current.items():
        if scope not in _ALLOWED_SCOPES or not isinstance(asset_id, str):
            raise VisualIdentityError("CURRENT_ASSET_INVALID")
        asset = by_id.get(asset_id)
        if asset is None or asset.scope != scope or asset.state != "CANONICAL":
            raise VisualIdentityError("CURRENT_ASSET_INVALID")

    for scope, asset_id in profile.active_candidate.items():
        if scope not in _ALLOWED_SCOPES or not isinstance(asset_id, str):
            raise VisualIdentityError("ACTIVE_CANDIDATE_INVALID")
        asset = by_id.get(asset_id)
        if asset is None or asset.scope != scope or asset.state != "CANDIDATE":
            raise VisualIdentityError("ACTIVE_CANDIDATE_INVALID")

    return profile


def load_visual_profile(path: str | Path) -> VisualProfile:
    """Load and validate a visual profile from a JSON file.
    
    If the file is named profile.json, its parent directory name must match
    the profile's identity_id.
    
    Args:
        path: Path to the profile JSON file.
        
    Returns:
        A validated VisualProfile.
        
    Raises:
        FileNotFoundError: If the file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
        VisualIdentityError: If the profile is invalid or path constraints are violated.
    """
    profile_path = Path(path)
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    profile = validate_visual_profile(data)
    if profile_path.name == "profile.json" and profile_path.parent.name != profile.identity_id:
        raise VisualIdentityError("PROFILE_PATH_IDENTITY_MISMATCH")
    return profile


def resolve_bus_avatar(
    profile: VisualProfile | Mapping[str, Any], *, allow_candidate: bool = False
) -> VisualAsset | None:
    """Resolve the bus avatar asset for a visual profile.
    
    Returns the CANONICAL bus_avatar asset if available. If allow_candidate=True,
    falls back to the CANDIDATE bus_avatar asset if no CANONICAL asset exists.
    
    Args:
        profile: A VisualProfile or raw profile dict to validate.
        allow_candidate: If True, fall back to active_candidate.bus_avatar
            when no current canonical asset is available.
        
    Returns:
        The resolved VisualAsset, or None if no bus_avatar asset is available.
        
    Raises:
        VisualIdentityError: If the profile fails validation.
    """
    validated = validate_visual_profile(profile)
    by_id = {asset.asset_id: asset for asset in validated.assets}

    canonical_id = validated.current.get("bus_avatar")
    if canonical_id:
        return by_id[canonical_id]

    if allow_candidate:
        candidate_id = validated.active_candidate.get("bus_avatar")
        if candidate_id:
            return by_id[candidate_id]

    return None
