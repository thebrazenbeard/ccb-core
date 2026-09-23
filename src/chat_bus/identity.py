"""Logical identity addressing for Chat Bus transports.

This module provides a canonical form for logical addresses used within
Chat Bus transports and an immutable registry of endpoints that prevents
ambiguous or colliding addresses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Final, Iterable, Iterator, Optional, Pattern, Tuple

_ADDRESS_RE: Final[Pattern[str]] = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SLACK_USER_RE: Final[Pattern[str]] = re.compile(r"^[UW][A-Z0-9]+$")


class IdentityError(ValueError):
    """Raised when logical endpoint configuration is ambiguous or invalid."""


def normalize_address(value: str) -> str:
    """Normalize a user-facing logical address to its canonical lookup key.

    - Strips leading/trailing whitespace
    - Removes a leading '@'
    - Performs casefold()
    - Validates against the canonical address regex
    """
    if not isinstance(value, str):
        raise IdentityError("ADDRESS_MUST_BE_TEXT")
    address = value.strip()
    if address.startswith("@"):
        address = address[1:]
    address = address.casefold()
    if not _ADDRESS_RE.fullmatch(address):
        raise IdentityError("INVALID_LOGICAL_ADDRESS")
    return address


@dataclass(frozen=True)
class LogicalEndpoint:
    """A stable Chat Bus endpoint that may be rendered through multiple transports.

    endpoint_id is expected to already be the canonical form (lowercased, no leading '@').
    Use LogicalEndpoint.create(...) to build an endpoint from user input that may not be canonical.
    """
    endpoint_id: str
    display_name: str
    project_scope: str
    aliases: Tuple[str, ...] = field(default_factory=tuple)
    slack_user_id: Optional[str] = None
    icon_emoji: Optional[str] = None

    def __post_init__(self) -> None:
        # Enforce endpoint_id is canonical (explicit invariant)
        canonical = normalize_address(self.endpoint_id)
        if canonical != self.endpoint_id:
            raise IdentityError("ENDPOINT_ID_MUST_BE_CANONICAL")

        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise IdentityError("DISPLAY_NAME_REQUIRED")
        if not isinstance(self.project_scope, str) or not self.project_scope.strip():
            raise IdentityError("PROJECT_SCOPE_REQUIRED")

        if self.slack_user_id is not None and (
            not isinstance(self.slack_user_id, str)
            or _SLACK_USER_RE.fullmatch(self.slack_user_id) is None
        ):
            raise IdentityError("INVALID_SLACK_USER_ID")

        if self.icon_emoji is not None:
            if not isinstance(self.icon_emoji, str) or not (
                len(self.icon_emoji) >= 3
                and self.icon_emoji.startswith(":")
                and self.icon_emoji.endswith(":")
            ):
                raise IdentityError("INVALID_ICON_EMOJI")

        seen: set[str] = set()
        for alias in self.aliases:
            normalized = normalize_address(alias)
            if normalized == canonical or normalized in seen:
                raise IdentityError("DUPLICATE_ENDPOINT_ALIAS")
            seen.add(normalized)

    @classmethod
    def create(cls,
               endpoint_id: str,
               display_name: str,
               project_scope: str,
               aliases: Iterable[str] = (),
               slack_user_id: Optional[str] = None,
               icon_emoji: Optional[str] = None) -> "LogicalEndpoint":
        """Factory that accepts user input (non-canonical addresses) and returns a canonicalized LogicalEndpoint.

        This avoids forcing callers to pre-normalize endpoint_id/aliases.
        """
        canonical_id = normalize_address(endpoint_id)
        canonical_aliases = tuple(normalize_address(a) for a in aliases)
        return cls(
            endpoint_id=canonical_id,
            display_name=display_name,
            project_scope=project_scope,
            aliases=canonical_aliases,
            slack_user_id=slack_user_id,
            icon_emoji=icon_emoji,
        )

    @property
    def addresses(self) -> Tuple[str, ...]:
        """All canonical addresses that refer to this endpoint (id + aliases)."""
        # endpoint_id is canonical by invariant; still safe to normalize for robustness
        return (self.endpoint_id, *(normalize_address(alias) for alias in self.aliases))


class EndpointRegistry:
    """Immutable alias registry that rejects ambiguous logical addresses.

    Provides mapping-like helpers:
      - resolve(address)  -> raises on unknown
      - get(address)      -> returns None on unknown
      - resolve_slack_user(slack_user_id)
      - lookup by endpoint id via registry[endpoint_id]
      - iteration over endpoint ids
    """

    def __init__(self, endpoints: Iterable[LogicalEndpoint]) -> None:
        by_id: dict[str, LogicalEndpoint] = {}
        by_address: dict[str, LogicalEndpoint] = {}
        by_slack_user: dict[str, LogicalEndpoint] = {}

        for endpoint in endpoints:
            if endpoint.endpoint_id in by_id:
                raise IdentityError("DUPLICATE_ENDPOINT_ID")
            by_id[endpoint.endpoint_id] = endpoint

            for address in endpoint.addresses:
                if address in by_address:
                    raise IdentityError("LOGICAL_ADDRESS_COLLISION")
                by_address[address] = endpoint

            if endpoint.slack_user_id is not None:
                if endpoint.slack_user_id in by_slack_user:
                    raise IdentityError("SLACK_USER_ID_COLLISION")
                by_slack_user[endpoint.slack_user_id] = endpoint

        if not by_id:
            raise IdentityError("AT_LEAST_ONE_ENDPOINT_REQUIRED")

        # Freeze internal maps
        self._by_id = dict(by_id)
        self._by_address = dict(by_address)
        self._by_slack_user = dict(by_slack_user)

    # Mapping-like behavior (by endpoint_id)
    def __getitem__(self, endpoint_id: str) -> LogicalEndpoint:
        return self._by_id[endpoint_id]

    def __contains__(self, endpoint_id: object) -> bool:
        return isinstance(endpoint_id, str) and endpoint_id in self._by_id

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._by_id))

    # Lookup helpers
    def resolve(self, address: str) -> LogicalEndpoint:
        """Resolve an address; raises IdentityError on unknown or invalid address."""
        try:
            key = normalize_address(address)
        except IdentityError as exc:
            raise IdentityError("UNKNOWN_LOGICAL_ENDPOINT") from exc
        endpoint = self._by_address.get(key)
        if endpoint is None:
            raise IdentityError("UNKNOWN_LOGICAL_ENDPOINT")
        return endpoint

    def get(self, address: str) -> Optional[LogicalEndpoint]:
        """Resolve an address returning None on unknown or invalid addresses."""
        try:
            key = normalize_address(address)
        except IdentityError:
            return None
        return self._by_address.get(key)

    def resolve_slack_user(self, slack_user_id: str) -> LogicalEndpoint:
        endpoint = self._by_slack_user.get(slack_user_id)
        if endpoint is None:
            raise IdentityError("UNKNOWN_SLACK_USER_ENDPOINT")
        return endpoint

    def endpoint_ids(self) -> Tuple[str, ...]:
        """Return a sorted tuple of endpoint IDs stored in the registry."""
        return tuple(sorted(self._by_id))
