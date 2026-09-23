"""Pure provider-projection commands for Radar's Supabase adapter.

This module contains no credentials and performs no network I/O. It translates
Radar state into parameterized, schema-scoped commands that an operator/runtime
may execute through an authorized provider client.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


_ALLOWED_SCHEMAS = {"radar"}
_ALLOWED_ENDPOINT_STATUSES = frozenset({"ACTIVE", "DEGRADED", "OFFLINE", "ARCHIVED"})
_MIN_PRIORITY = 0
_MAX_PRIORITY = 4


class ProjectionError(ValueError):
    """Deterministic validation failure at the provider-projection boundary."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ProjectionCommand:
    operation: str
    schema: str
    sql: str
    parameters: dict[str, object]


@dataclass(frozen=True)
class ProviderSnapshot:
    protocol_head: str | None
    identities: tuple[dict[str, object], ...]
    canonical: bool = False


def build_projection_commands(state: Mapping[str, object]) -> tuple[ProjectionCommand, ...]:
    """Build validated, parameterized projection commands from Radar state."""
    if not isinstance(state, Mapping):
        raise ProjectionError("INVALID_STATE", f"state must be a Mapping, got {type(state).__name__}")

    commands: list[ProjectionCommand] = []

    identities = _collection(state.get("identities"), "INVALID_IDENTITIES")
    for raw in identities:
        record = _mapping(raw, "IDENTITY_PROJECTION_MUST_BE_MAPPING")
        identity_id = _required_text(record, "identity_id", "IDENTITY_ID_REQUIRED")
        display_name = _required_text(record, "display_name", "DISPLAY_NAME_REQUIRED")
        commands.append(
            _build_upsert_command(
                "UPSERT_IDENTITY",
                "radar",
                (
                    "insert into radar.identities (identity_id, display_name) "
                    "values (%(identity_id)s, %(display_name)s) "
                    "on conflict (identity_id) do update set display_name = excluded.display_name, updated_at = now()"
                ),
                {"identity_id": identity_id.casefold(), "display_name": display_name},
            )
        )

    endpoints = _collection(state.get("endpoints"), "INVALID_ENDPOINTS")
    for raw in endpoints:
        record = _mapping(raw, "ENDPOINT_PROJECTION_MUST_BE_MAPPING")
        endpoint_id = _required_text(record, "endpoint_id", "ENDPOINT_ID_REQUIRED")
        identity_id = _required_text(record, "identity_id", "IDENTITY_ID_REQUIRED")
        transport = _required_text(record, "transport", "TRANSPORT_REQUIRED")
        address = _required_text(record, "address", "ADDRESS_REQUIRED")
        status_obj = record.get("status", "ACTIVE")
        if not isinstance(status_obj, str) or not status_obj.strip():
            raise ProjectionError("INVALID_ENDPOINT_STATUS", "status must be a non-empty string")
        status = status_obj.strip().upper()
        if status not in _ALLOWED_ENDPOINT_STATUSES:
            raise ProjectionError(
                "INVALID_ENDPOINT_STATUS",
                f"status must be one of {sorted(_ALLOWED_ENDPOINT_STATUSES)}, got {status}",
            )
        commands.append(
            _build_upsert_command(
                "UPSERT_ENDPOINT",
                "radar",
                (
                    "insert into radar.endpoints (endpoint_id, identity_id, transport, address, status) "
                    "values (%(endpoint_id)s, %(identity_id)s, %(transport)s, %(address)s, %(status)s) "
                    "on conflict (endpoint_id) do update set identity_id = excluded.identity_id, "
                    "transport = excluded.transport, address = excluded.address, status = excluded.status, updated_at = now()"
                ),
                {
                    "endpoint_id": endpoint_id,
                    "identity_id": identity_id.casefold(),
                    "transport": transport.casefold(),
                    "address": address,
                    "status": status,
                },
            )
        )

    subscriptions = _collection(state.get("subscriptions"), "INVALID_SUBSCRIPTIONS")
    for raw in subscriptions:
        record = _mapping(raw, "SUBSCRIPTION_PROJECTION_MUST_BE_MAPPING")
        identity_id = _required_text(record, "identity_id", "IDENTITY_ID_REQUIRED")
        domain = _required_text(record, "domain", "DOMAIN_REQUIRED")
        intent_obj = record.get("intent")
        if intent_obj is not None and not isinstance(intent_obj, str):
            raise ProjectionError("INVALID_INTENT", f"intent must be a string, got {type(intent_obj).__name__}")
        intent = intent_obj.strip().casefold() if isinstance(intent_obj, str) and intent_obj.strip() else None

        priority_obj = record.get("min_priority", 3)
        if isinstance(priority_obj, bool):
            raise ProjectionError("INVALID_PRIORITY", "min_priority must be an integer from 0 through 4")
        try:
            min_priority = int(priority_obj)
        except (TypeError, ValueError) as exc:
            raise ProjectionError(
                "INVALID_PRIORITY",
                f"min_priority must be an integer from {_MIN_PRIORITY} through {_MAX_PRIORITY}",
            ) from exc
        if not _MIN_PRIORITY <= min_priority <= _MAX_PRIORITY:
            raise ProjectionError(
                "INVALID_PRIORITY",
                f"min_priority must be between {_MIN_PRIORITY} and {_MAX_PRIORITY}, got {min_priority}",
            )

        active_obj = record.get("active", True)
        if not isinstance(active_obj, bool):
            raise ProjectionError("INVALID_ACTIVE", "active must be a boolean")

        commands.append(
            _build_upsert_command(
                "UPSERT_SUBSCRIPTION",
                "radar",
                (
                    "insert into radar.subscriptions (identity_id, domain, intent, min_priority, active) "
                    "values (%(identity_id)s, %(domain)s, %(intent)s, %(min_priority)s, %(active)s) "
                    "on conflict (identity_id, domain, coalesce(intent, ''), min_priority) "
                    "do update set active = excluded.active"
                ),
                {
                    "identity_id": identity_id.casefold(),
                    "domain": domain.casefold(),
                    "intent": intent,
                    "min_priority": min_priority,
                    "active": active_obj,
                },
            )
        )

    return tuple(commands)


def parse_provider_snapshot(raw: Mapping[str, object]) -> ProviderSnapshot:
    """Parse provider state without promoting it to canonical truth."""
    if not isinstance(raw, Mapping):
        raise ProjectionError("INVALID_PROVIDER_SNAPSHOT", "provider snapshot must be a Mapping")
    protocol = raw.get("protocol_head")
    if protocol is not None and not isinstance(protocol, str):
        raise ProjectionError("INVALID_PROTOCOL_HEAD", f"protocol_head must be a string, got {type(protocol).__name__}")
    identities_obj = _collection(raw.get("identities"), "INVALID_IDENTITIES")
    identities = tuple(
        dict(_mapping(item, "INVALID_IDENTITY_SNAPSHOT"))
        for item in identities_obj
    )
    return ProviderSnapshot(protocol_head=protocol, identities=identities, canonical=False)


def _collection(value: object, error: str) -> tuple[object, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ProjectionError(error, f"expected list or tuple, got {type(value).__name__}")
    return tuple(value)


def _build_upsert_command(
    operation: str,
    schema: str,
    sql: str,
    parameters: dict[str, object],
) -> ProjectionCommand:
    if schema not in _ALLOWED_SCHEMAS:
        raise ProjectionError("INVALID_SCHEMA", f"schema must be one of {_ALLOWED_SCHEMAS}, got {schema}")
    return ProjectionCommand(operation=operation, schema=schema, sql=sql, parameters=parameters)


def _mapping(value: object, error: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProjectionError(error, f"expected Mapping, got {type(value).__name__}")
    return value


def _required_text(record: Mapping[str, object], key: str, error: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProjectionError(error, f"field '{key}' must be a non-empty string")
    return value.strip()


__all__ = [
    "ProjectionCommand",
    "ProviderSnapshot",
    "ProjectionError",
    "build_projection_commands",
    "parse_provider_snapshot",
]
