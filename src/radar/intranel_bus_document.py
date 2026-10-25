"""Bounded INTRANEL/1 carriage through the *actual* Bus writer-lane grammar.

This is only a *source-stage document subprofile*. It uses namespaced Base64URL
headers because the Bus already owns the outer ``message_id`` header; it is
not a drop-in implementation of Intranel's BUS_PROFILE_V1 metadata wrapper.
A trusted runtime must independently bind the authenticated writer/lane and
semantic-target-to-recipient identity mapping, append by SafeLaneAppender,
route the result, and invoke official Intranel ``parse_message`` plus the full
receiver-side admission policy. A well-formed document is not authenticated,
semantically admitted, or authorized here. A claim of ``RECEIPT`` is not an
effect readback. Operation-level replay protection is external and durable.
This codec is opt-in; ``SafeLaneAppender`` also accepts unrelated ordinary
Bus documents, so an untrusted sender's bypass of this codec must not grant
Intranel admission or an authorized effect at the receiving endpoint.

The Bus's ``message_id`` is writer-local and never equals the semantic
Intranel packet ``message_id``. The exact canonical semantic JSON is the body
of record; its digest/identity are independently checked on readback.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from typing import Any

from .message_grammar import MessageGrammarError, parse_bus_document


_IDENTITY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_BUS_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*-[A-Za-z0-9][A-Za-z0-9._-]*$")
_ROUTE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_ADDRESS = re.compile(r"^[a-z][a-z0-9_-]*:[A-Za-z0-9][A-Za-z0-9._/-]*$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_PERFORMATIVES = frozenset(
    {"QUERY", "REPORT", "REQUEST", "REVIEW", "ACK", "REJECT", "WAIT", "CONFLICT", "RECEIPT"}
)
# The outbound document carries a fully materialized IntranelMessage.to_mapping()
# object, NOT an arbitrary partial input to parse_message(). These are the
# complete fields in source-bound INTRANEL/1 (main@25a2332). The actual
# receiver still parses/admit-checks the typed message independently.
_INTRANEL_CANONICAL_FIELDS = frozenset({
    "protocol", "origin", "actor", "target", "reply_to", "message_id",
    "operation_id", "target_operation_id", "parent_message_id",
    "conversation_id", "performative", "subject", "exact_subject", "payload",
    "authority_claim_ref", "constraints", "prohibited_effects", "expected_response",
    "ack_required", "observed_at", "expires_at", "idempotency_key", "priority",
    "effect_class", "status", "error", "receipt", "security_profile",
    "capabilities", "provenance",
})
_HEADER_NAMES = frozenset(
    {
        "message_id", "prev_message_id", "writer", "intended_recipient", "created_at",
        "requires_ack", "intranel_protocol", "intranel_message_id_b64u",
        "intranel_operation_id_b64u", "intranel_origin", "intranel_actor",
        "intranel_target", "semantic_sha256",
    }
)
_MAX_SEMANTIC_BYTES = 65_536
_MAX_DOCUMENT_BYTES = 68_608
_MAX_BUS_ID_BYTES = 256
_SAFE_JSON_INTEGER = (1 << 53) - 1


class IntranelBusCarriageError(ValueError):
    """A source-stage Bus document must be held, not dispatched or admitted."""


@dataclass(frozen=True)
class DecodedIntranelBusDocument:
    bus_message_id: str
    prev_bus_message_id: str | None
    writer: str
    recipient: str
    semantic_message_id: str
    operation_id: str | None
    origin: str
    actor: str
    target: str
    semantic_sha256: str
    canonical_message_bytes: bytes


def _require(condition: bool, detail: str) -> None:
    if not condition:
        raise IntranelBusCarriageError(detail)


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise IntranelBusCarriageError("DUPLICATE_SEMANTIC_KEY")
        value[key] = item
    return value


def _safe_json(value: Any, *, depth: int = 0, nodes: list[int] | None = None) -> None:
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    _require(nodes[0] <= 4_096 and depth <= 24, "SEMANTIC_COMPLEXITY_EXCEEDED")
    if value is None or isinstance(value, (str, bool)):
        if isinstance(value, str):
            try:
                encoded = value.encode("utf-8", "strict")
            except UnicodeError as exc:
                raise IntranelBusCarriageError("SEMANTIC_UNICODE_INVALID") from exc
            _require(len(encoded) <= 8_192, "SEMANTIC_STRING_TOO_LONG")
        return
    if type(value) is int:
        _require(-_SAFE_JSON_INTEGER <= value <= _SAFE_JSON_INTEGER, "SEMANTIC_NUMBER_UNSAFE")
        return
    if type(value) is list:
        _require(len(value) <= 256, "SEMANTIC_COLLECTION_TOO_LARGE")
        for item in value:
            _safe_json(item, depth=depth + 1, nodes=nodes)
        return
    if type(value) is dict:
        _require(len(value) <= 256, "SEMANTIC_COLLECTION_TOO_LARGE")
        for key, item in value.items():
            _require(
                isinstance(key, str) and key and len(key.encode("utf-8")) <= 256
                and all(0x20 <= ord(c) <= 0x7E for c in key),
                "SEMANTIC_KEY_INVALID",
            )
            _safe_json(item, depth=depth + 1, nodes=nodes)
        return
    raise IntranelBusCarriageError("SEMANTIC_JSON_TYPE_INVALID")


def _required_token(raw: Any, name: str, *, max_bytes: int = 256) -> str:
    _require(
        isinstance(raw, str) and bool(raw) and not any(ch.isspace() for ch in raw),
        f"{name.upper()}_INVALID",
    )
    _require(len(raw.encode("utf-8")) <= max_bytes, f"{name.upper()}_TOO_LONG")
    return raw


def _address(raw: Any, name: str) -> str:
    _require(isinstance(raw, str) and len(raw) <= 320 and bool(_ADDRESS.fullmatch(raw)),
             f"{name.upper()}_INVALID")
    return raw


def _semantic(canonical: bytes) -> dict[str, Any]:
    _require(isinstance(canonical, bytes) and len(canonical) <= _MAX_SEMANTIC_BYTES,
             "SEMANTIC_BYTES_INVALID")
    try:
        decoded = canonical.decode("utf-8", "strict")
        raw = json.loads(decoded, object_pairs_hook=_no_duplicate_keys)
        _safe_json(raw)
        canon = json.dumps(raw, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False).encode("utf-8", "strict")
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise IntranelBusCarriageError("SEMANTIC_JSON_INVALID") from exc
    _require(isinstance(raw, dict) and canon == canonical, "SEMANTIC_NONCANONICAL")
    _require(set(raw) == _INTRANEL_CANONICAL_FIELDS,
             "INTRANEL_CANONICAL_FIELDS_MISSING_OR_UNKNOWN")
    _require(raw.get("protocol") == "INTRANEL/1", "PROTOCOL_INVALID")
    _required_token(raw.get("message_id"), "message_id")
    _required_token(raw.get("conversation_id"), "conversation_id")
    for name in ("operation_id", "target_operation_id", "parent_message_id", "idempotency_key"):
        if raw.get(name) is not None:
            _required_token(raw[name], name)
    for name in ("origin", "actor", "target", "reply_to"):
        _address(raw.get(name), name)
    _require(raw.get("security_profile") == "OPEN", "PRIVATE_SECURITY_UNSUPPORTED")
    _require(raw.get("effect_class") == "READ_ONLY", "MUTATION_UNSUPPORTED")
    _require(isinstance(raw.get("performative"), str)
             and raw["performative"] in _PERFORMATIVES, "PERFORMATIVE_UNSUPPORTED")
    _require(type(raw.get("priority")) is int and 0 <= raw["priority"] <= 4,
             "PRIORITY_INVALID")
    _require(raw["expected_response"] is None
             or (isinstance(raw["expected_response"], str)
                 and raw["expected_response"] in {
                     "QUERY", "REPORT", "REQUEST", "EXECUTE", "REVIEW", "ACK",
                     "REJECT", "WAIT", "CONFLICT", "CANCEL", "RECEIPT",
                 }), "EXPECTED_RESPONSE_INVALID")
    for name in ("subject", "exact_subject", "authority_claim_ref", "status", "error"):
        value = raw[name]
        _require(value is None or (isinstance(value, str) and bool(value)
                 and len(value.encode("utf-8")) <= 4_096), f"{name.upper()}_INVALID")
    for name in ("constraints", "prohibited_effects", "capabilities", "provenance"):
        value = raw[name]
        _require(isinstance(value, list) and len(value) <= 64
                 and all(isinstance(item, str) and bool(item)
                         and len(item.encode("utf-8")) <= 4_096 for item in value),
                 f"{name.upper()}_INVALID")
    if raw["performative"] == "REVIEW":
        _require(bool(raw["exact_subject"]), "REVIEW_EXACT_SUBJECT_REQUIRED")
    if raw["performative"] == "RECEIPT":
        _require(bool(raw["operation_id"]) and bool(raw["exact_subject"]),
                 "RECEIPT_EXACT_OPERATION_AND_SUBJECT_REQUIRED")
        _require(isinstance(raw["receipt"], dict), "RECEIPT_CLAIM_REQUIRED")
    else:
        _require(raw["receipt"] is None or isinstance(raw["receipt"], dict),
                 "RECEIPT_TYPE_INVALID")
    _require(type(raw.get("ack_required")) is bool,
             "ACK_TYPE_INVALID")
    _require(isinstance(raw.get("observed_at"), str) and bool(raw["observed_at"]),
             "OBSERVED_AT_REQUIRED")
    try:
        observed = datetime.fromisoformat(raw["observed_at"].replace("Z", "+00:00"))
        _require(observed.tzinfo is not None and observed.utcoffset() is not None,
                 "OBSERVED_AT_OFFSET_REQUIRED")
        if raw["expires_at"] is not None:
            _require(isinstance(raw["expires_at"], str), "EXPIRES_AT_INVALID")
            expires = datetime.fromisoformat(raw["expires_at"].replace("Z", "+00:00"))
            _require(expires.tzinfo is not None and expires.utcoffset() is not None
                     and expires > observed, "EXPIRES_AT_INVALID")
    except ValueError as exc:
        raise IntranelBusCarriageError("SEMANTIC_TIMESTAMP_INVALID") from exc
    return raw


def _id_b64(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _check_writer_id(lane_identity: str, bus_message_id: str) -> None:
    _require(isinstance(lane_identity, str) and bool(_IDENTITY.fullmatch(lane_identity)),
             "LANE_IDENTITY_INVALID")
    _require(isinstance(bus_message_id, str) and len(bus_message_id) <= _MAX_BUS_ID_BYTES
             and bool(_BUS_ID.fullmatch(bus_message_id))
             and bus_message_id.startswith(f"{lane_identity}-"), "BUS_MESSAGE_ID_NOT_WRITER_LOCAL")


def encode_intranel_bus_document(
    canonical_message_bytes: bytes,
    *,
    lane_identity: str,
    writer: str,
    bus_message_id: str,
    intended_recipient: str,
    prev_bus_message_id: str | None = None,
) -> bytes:
    """Create source-only bytes; this function never appends or routes.

    ``prev_bus_message_id`` is the trusted writer-lane history tip for the
    *outer* Bus chain, unrelated to Intranel's semantic parent_message_id.
    The caller must acquire that history through its normal control plane.
    """
    raw = _semantic(canonical_message_bytes)
    _check_writer_id(lane_identity, bus_message_id)
    if prev_bus_message_id is not None:
        _check_writer_id(lane_identity, prev_bus_message_id)
        _require(prev_bus_message_id != bus_message_id, "BUS_PREDECESSOR_SELF_REFERENCE")
    _require(isinstance(writer, str) and bool(writer) and writer.strip() == writer
             and len(writer) <= 128
             and all(0x20 <= ord(c) <= 0x7E and c not in "`\"'" for c in writer),
             "WRITER_INVALID")
    _require(isinstance(intended_recipient, str)
             and bool(_ROUTE.fullmatch(intended_recipient)), "RECIPIENT_INVALID")
    _require(bus_message_id != raw["message_id"], "PACKET_AND_BUS_ID_COLLISION")
    from .message_grammar import normalize_bus_timestamp
    try:
        created_at = normalize_bus_timestamp(raw["observed_at"])
    except MessageGrammarError as exc:
        raise IntranelBusCarriageError("OBSERVED_AT_INVALID") from exc
    _require(created_at == raw["observed_at"], "OBSERVED_AT_NOT_EXACT")
    digest = hashlib.sha256(canonical_message_bytes).hexdigest()
    fields = [
        ("message_id", bus_message_id),
        ("writer", writer),
        ("intended_recipient", intended_recipient),
        ("created_at", created_at),
        ("requires_ack", "true" if raw.get("ack_required") else "false"),
        ("intranel_protocol", "INTRANEL/1"),
        ("intranel_message_id_b64u", _id_b64(raw["message_id"])),
        ("intranel_origin", raw["origin"]),
        ("intranel_actor", raw["actor"]),
        ("intranel_target", raw["target"]),
        ("semantic_sha256", digest),
    ]
    if prev_bus_message_id is not None:
        fields.append(("prev_message_id", prev_bus_message_id))
    if raw.get("operation_id") is not None:
        fields.append(("intranel_operation_id_b64u", _id_b64(raw["operation_id"])))
    result = ("\n".join(f"{key}: {value}" for key, value in fields) + "\n\n").encode("utf-8") + canonical_message_bytes
    _require(len(result) <= _MAX_DOCUMENT_BYTES, "BUS_DOCUMENT_SIZE_INVALID")
    return result


def decode_intranel_bus_document(
    document: bytes,
    *,
    expected_lane_identity: str,
    expected_writer: str,
    expected_recipient: str,
    expected_target: str,
    expected_prev_bus_message_id: str | None = None,
) -> DecodedIntranelBusDocument:
    """Check byte/header bindings against independently trusted routing inputs.

    ``expected_writer`` must come from authenticated lane custody and
    ``expected_recipient``/``expected_target`` from trusted routing state,
    never from the untrusted message itself. This is not receiver admission.
    """
    _require(isinstance(document, bytes) and len(document) <= _MAX_DOCUMENT_BYTES,
             "BUS_DOCUMENT_SIZE_INVALID")
    try:
        parsed = parse_bus_document(document)
    except MessageGrammarError as exc:
        raise IntranelBusCarriageError("BUS_GRAMMAR_INVALID") from exc
    _check_writer_id(expected_lane_identity, parsed.message_id)
    if expected_prev_bus_message_id is not None:
        _check_writer_id(expected_lane_identity, expected_prev_bus_message_id)
    _require(parsed.headers.get("prev_message_id") == expected_prev_bus_message_id,
             "BUS_PREDECESSOR_MISMATCH")
    _require(parsed.writer == expected_writer, "BUS_WRITER_MISMATCH")
    _require(parsed.intended_recipient == expected_recipient, "BUS_RECIPIENT_MISMATCH")
    _require(set(parsed.headers) <= _HEADER_NAMES, "BUS_UNEXPECTED_HEADER")
    semantic = parsed.body.encode("utf-8")
    raw = _semantic(semantic)
    _require(raw["target"] == expected_target, "SEMANTIC_TARGET_MISMATCH")
    digest = hashlib.sha256(semantic).hexdigest()
    expected = encode_intranel_bus_document(
        semantic, lane_identity=expected_lane_identity, writer=expected_writer,
        bus_message_id=parsed.message_id, intended_recipient=expected_recipient,
        prev_bus_message_id=expected_prev_bus_message_id,
    )
    # Complete byte comparison detects inserted, dropped, duplicated, or
    # contradictory wrapper headers, not only digest or parsed-body equality.
    _require(document == expected, "SEMANTIC_BUS_WRAPPER_CONFLICT")
    _require(bool(_DIGEST.fullmatch(digest)), "SEMANTIC_DIGEST_INVALID")
    return DecodedIntranelBusDocument(
        bus_message_id=parsed.message_id,
        prev_bus_message_id=expected_prev_bus_message_id,
        writer=parsed.writer,
        recipient=parsed.intended_recipient,
        semantic_message_id=raw["message_id"],
        operation_id=raw.get("operation_id"),
        origin=raw["origin"], actor=raw["actor"], target=raw["target"],
        semantic_sha256=digest,
        canonical_message_bytes=semantic,
    )


__all__ = ["DecodedIntranelBusDocument", "IntranelBusCarriageError",
           "decode_intranel_bus_document", "encode_intranel_bus_document"]
