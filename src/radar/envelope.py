"""Versioned, transport-neutral Radar message envelope."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import uuid
from typing import Any, List, Mapping, Optional, Tuple

from .model import EnvelopeClassification


ALLOWED_DOMAINS = frozenset(
    {
        "system",
        "llm",
        "ui",
        "database",
        "coordination",
        "security",
        "chat_bus",
        "protocol",
        "repository",
    }
)
ALLOWED_INTENTS = frozenset(
    {
        "command",
        "query",
        "event",
        "error",
        "telemetry",
        "verification",
        "bus_message",
    }
)

DEFAULT_PRIORITY = 3
MIN_PRIORITY = 0
MAX_PRIORITY = 4

# These are pre-serialization budgets for admitted JSON wire payloads, not
# limits for the separately sanitized, durable unknown-taxonomy DLQ records.
MAX_WIRE_DEPTH = 24
MAX_WIRE_WIDTH = 128
MAX_WIRE_NODES = 2_048
MAX_WIRE_STRING_CHARS = 4_096
MAX_WIRE_ESTIMATED_BYTES = 65_536


def _payload_fits_budget(payload: object) -> bool:
    """Bound a native JSON tree before json.dumps or metadata normalization.

    The conservative estimate accounts for worst-case JSON character escaping.
    Only native JSON tree types are accepted; arbitrary Python Mapping objects
    can lie about their size or run code during iteration/serialization.
    """
    stack: list[tuple[object, int, bool]] = [(payload, 0, False)]
    ancestors: set[int] = set()
    nodes = 0
    estimated = 0
    while stack:
        value, depth, leaving = stack.pop()
        if leaving:
            ancestors.remove(id(value))
            continue
        nodes += 1
        if nodes > MAX_WIRE_NODES or depth >= MAX_WIRE_DEPTH:
            return False
        if value is None or type(value) is bool:
            estimated += 5
        elif type(value) is str:
            if len(value) > MAX_WIRE_STRING_CHARS:
                return False
            estimated += len(value) * 6 + 2
        elif type(value) is int:
            if value.bit_length() > MAX_WIRE_STRING_CHARS * 4:
                return False
            estimated += value.bit_length() // 3 + 2
        elif type(value) is float:
            if not math.isfinite(value):
                return False
            estimated += 32
        elif type(value) in (dict, list, tuple):
            if len(value) > MAX_WIRE_WIDTH or id(value) in ancestors:
                return False
            ancestors.add(id(value))
            stack.append((value, depth, True))
            estimated += len(value) * 2 + 2
            if type(value) is dict:
                for key, item in value.items():
                    if type(key) is not str or len(key) > MAX_WIRE_STRING_CHARS:
                        return False
                    estimated += len(key) * 6 + 3
                    stack.append((item, depth + 1, False))
            else:
                for item in value:
                    stack.append((item, depth + 1, False))
        else:
            return False
        if estimated > MAX_WIRE_ESTIMATED_BYTES:
            return False
    return True


@dataclass(frozen=True)
class RadarEnvelope:
    schema_version: int
    message_id: str
    created_at: str
    sender: str
    audience: Tuple[str, ...]
    domain: str
    intent: str
    priority: int
    payload: Any
    content_hash: str
    idempotency_key: str
    correlation_id: Optional[str] = None
    root_task_id: Optional[str] = None
    causal_parent_id: Optional[str] = None
    requires_ack: bool = False
    expires_at: Optional[str] = None
    authority_ref: Optional[str] = None
    source_refs: Tuple[str, ...] = ()

    def to_dict(self) -> Mapping[str, Any]:
        """Return a JSON-serializable mapping of this envelope."""
        return {
            "schema_version": self.schema_version,
            "message_id": self.message_id,
            "created_at": self.created_at,
            "sender": self.sender,
            "audience": list(self.audience),
            "domain": self.domain,
            "intent": self.intent,
            "priority": self.priority,
            "payload": self.payload,
            "content_hash": self.content_hash,
            "idempotency_key": self.idempotency_key,
            "correlation_id": self.correlation_id,
            "root_task_id": self.root_task_id,
            "causal_parent_id": self.causal_parent_id,
            "requires_ack": self.requires_ack,
            "expires_at": self.expires_at,
            "authority_ref": self.authority_ref,
            "source_refs": list(self.source_refs),
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "EnvelopeResult":
        """Validate native JSON wire data before normalizing a RadarEnvelope."""
        if type(raw) is not dict:
            return EnvelopeResult(
                EnvelopeClassification.REJECTED, None, ("envelope",)
            )
        errors: List[str] = []
        required = ("schema_version", "sender", "domain", "intent", "payload")
        for field in required:
            if field not in raw:
                errors.append(field)

        if errors:
            return EnvelopeResult(EnvelopeClassification.REJECTED, None, tuple(errors))

        # Wire schema numbers are exact JSON integers. Do not silently coerce
        # booleans, strings, or fractional floats through int(...).
        schema_raw = raw["schema_version"]
        if type(schema_raw) is not int or schema_raw != 1:
            return EnvelopeResult(
                EnvelopeClassification.REJECTED, None, ("schema_version",)
            )
        schema_version = schema_raw

        sender = _required_text(raw.get("sender"), "sender", errors, casefold=False)
        domain = _required_text(raw.get("domain"), "domain", errors, casefold=True)
        intent = _required_text(raw.get("intent"), "intent", errors, casefold=True)

        if errors:
            return EnvelopeResult(
                EnvelopeClassification.REJECTED,
                None,
                tuple(dict.fromkeys(errors)),
            )

        taxonomy_errors: List[str] = []
        if domain not in ALLOWED_DOMAINS:
            taxonomy_errors.append("domain")
        if intent not in ALLOWED_INTENTS:
            taxonomy_errors.append("intent")
        if taxonomy_errors:
            return EnvelopeResult(
                EnvelopeClassification.DLQ, None, tuple(taxonomy_errors)
            )

        # Unknown taxonomy is already routed to the bounded DLQ projection.
        # For valid taxonomy, reject over-budget JSON before materializing its
        # canonical serialization and idempotency hashes. First protect the
        # payload, then check the entire wire envelope (including audience,
        # source_refs, and optional metadata) against the aggregate byte budget.
        if not _payload_fits_budget(raw["payload"]):
            return EnvelopeResult(
                EnvelopeClassification.REJECTED, None, ("payload_budget",)
            )

        message_id_obj = raw.get("message_id")
        if isinstance(message_id_obj, str) and (
            type(message_id_obj) is not str or len(message_id_obj) > MAX_WIRE_STRING_CHARS
        ):
            return EnvelopeResult(
                EnvelopeClassification.REJECTED, None, ("message_id",)
            )
        if not _payload_fits_budget(raw):
            return EnvelopeResult(
                EnvelopeClassification.REJECTED, None, ("envelope_budget",)
            )

        normalized = False

        if isinstance(message_id_obj, str) and message_id_obj.strip():
            message_id = message_id_obj.strip()
        else:
            message_id = f"rad_{uuid.uuid4().hex[:16]}"
            normalized = True

        created_at_obj = raw.get("created_at")
        if isinstance(created_at_obj, str) and len(created_at_obj) > MAX_WIRE_STRING_CHARS:
            return EnvelopeResult(
                EnvelopeClassification.REJECTED, None, ("created_at",)
            )
        if isinstance(created_at_obj, str) and created_at_obj.strip():
            created_at = created_at_obj.strip()
        else:
            created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            normalized = True

        audience_obj = raw.get("audience")
        if audience_obj is None:
            audience: Tuple[str, ...] = ()
            normalized = True
        elif isinstance(audience_obj, (list, tuple)) and len(audience_obj) <= MAX_WIRE_WIDTH and all(
            isinstance(v, str) and v.strip() for v in audience_obj
        ):
            seen: set[str] = set()
            normalized_audience: List[str] = []
            for v in audience_obj:
                folded = v.strip().casefold()
                if folded not in seen:
                    seen.add(folded)
                    normalized_audience.append(folded)
            audience = tuple(normalized_audience)
        else:
            errors.append("audience")
            audience = ()

        if "priority" not in raw:
            priority = DEFAULT_PRIORITY
            normalized = True
        else:
            priority_obj = raw["priority"]
            if type(priority_obj) is not int:
                priority = MIN_PRIORITY - 1
                errors.append("priority")
            else:
                priority = priority_obj
                if not (MIN_PRIORITY <= priority <= MAX_PRIORITY):
                    errors.append("priority")

        requires_ack_obj = raw.get("requires_ack", False)
        if not isinstance(requires_ack_obj, bool):
            errors.append("requires_ack")
        requires_ack = bool(requires_ack_obj)

        source_refs_obj = raw.get("source_refs", ())
        if isinstance(source_refs_obj, (list, tuple)) and len(source_refs_obj) <= MAX_WIRE_WIDTH and all(
            isinstance(v, str) and bool(v.strip()) for v in source_refs_obj
        ):
            source_refs = tuple(v.strip() for v in source_refs_obj)
        else:
            if source_refs_obj is not None:
                errors.append("source_refs")
            source_refs = ()

        if errors:
            return EnvelopeResult(
                EnvelopeClassification.REJECTED,
                None,
                tuple(dict.fromkeys(errors)),
            )

        payload = raw["payload"]
        try:
            payload_json = json.dumps(payload, sort_keys=True)
        except (TypeError, ValueError, UnicodeError):
            return EnvelopeResult(
                EnvelopeClassification.REJECTED, None, ("payload",)
            )

        canonical = {
            "schema_version": schema_version,
            "sender": sender,
            "audience": audience,
            "domain": domain,
            "intent": intent,
            "payload": payload,
        }
        try:
            canonical_bytes = json.dumps(
                canonical,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            return EnvelopeResult(
                EnvelopeClassification.REJECTED, None, ("payload",)
            )
        content_hash = hashlib.sha256(canonical_bytes).hexdigest()

        # Preserve the original middleware contract: deterministic payload-only
        # hash for the 500 ms duplicate window. Caller-supplied keys are ignored.
        idempotency_key = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

        envelope = cls(
            schema_version=schema_version,
            message_id=message_id,
            created_at=created_at,
            sender=sender,
            audience=audience,
            domain=domain,
            intent=intent,
            priority=priority,
            payload=payload,
            content_hash=content_hash,
            idempotency_key=idempotency_key,
            correlation_id=_optional_text(raw.get("correlation_id")),
            root_task_id=_optional_text(raw.get("root_task_id")),
            causal_parent_id=_optional_text(raw.get("causal_parent_id")),
            requires_ack=requires_ack,
            expires_at=_optional_text(raw.get("expires_at")),
            authority_ref=_optional_text(raw.get("authority_ref")),
            source_refs=source_refs,
        )

        classification = (
            EnvelopeClassification.NORMALIZED
            if normalized
            else EnvelopeClassification.VALID
        )
        return EnvelopeResult(classification, envelope, ())


@dataclass(frozen=True)
class EnvelopeResult:
    classification: EnvelopeClassification
    envelope: Optional[RadarEnvelope]
    errors: Tuple[str, ...] = ()


def _required_text(
    value: object, field: str, errors: List[str], casefold: bool = True
) -> str:
    if type(value) is not str or len(value) > MAX_WIRE_STRING_CHARS or not value.strip():
        errors.append(field)
        return ""
    return value.strip().casefold() if casefold else value.strip()


def _optional_text(value: object) -> Optional[str]:
    if value is None:
        return None
    if type(value) is str and len(value) <= MAX_WIRE_STRING_CHARS and value.strip():
        return value.strip()
    return None


__all__ = [
    "ALLOWED_DOMAINS",
    "ALLOWED_INTENTS",
    "EnvelopeClassification",
    "EnvelopeResult",
    "RadarEnvelope",
]
