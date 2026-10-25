"""Source-stage INTRANEL/1 carriage across the actual Bus writer-lane grammar."""

import json

import pytest

from radar.intranel_bus_document import (
    IntranelBusCarriageError,
    decode_intranel_bus_document,
    encode_intranel_bus_document,
)
from radar.message_grammar import parse_bus_document


LANE = "agent_alpha"
WRITER = "Agent-Alpha"
RECIPIENT = "agent-beta"
TARGET = "agent:beta"
BUS_ID = "agent_alpha-bounded-001"


def semantic(**changes):
    raw = {
        "protocol": "INTRANEL/1",
        "message_id": "packet/../\u202ehidden",
        "operation_id": "op-001",
        "target_operation_id": None,
        "parent_message_id": None,
        "origin": "agent:alpha",
        "actor": "agent:alpha",
        "target": TARGET,
        "reply_to": "agent:alpha",
        "conversation_id": "case-1",
        "performative": "QUERY",
        "subject": None,
        "exact_subject": None,
        "authority_claim_ref": None,
        "constraints": [],
        "prohibited_effects": [],
        "expected_response": None,
        "effect_class": "READ_ONLY",
        "security_profile": "OPEN",
        "observed_at": "2026-10-25T03:00:00Z",
        "expires_at": None,
        "idempotency_key": None,
        "priority": 3,
        "status": None,
        "error": None,
        "receipt": None,
        "capabilities": [],
        "provenance": [],
        "ack_required": True,
        "payload": {"question": "status"},
    }
    raw.update(changes)
    return json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def encode(raw=None, **changes):
    args = {
        "lane_identity": LANE,
        "writer": WRITER,
        "bus_message_id": BUS_ID,
        "intended_recipient": RECIPIENT,
    }
    args.update(changes)
    return encode_intranel_bus_document(raw if raw is not None else semantic(), **args)


def decode(doc):
    return decode_intranel_bus_document(
        doc,
        expected_lane_identity=LANE,
        expected_writer=WRITER,
        expected_recipient=RECIPIENT,
        expected_target=TARGET,
    )


def test_exact_canonical_payload_is_distinct_from_writer_local_bus_message():
    raw = semantic()
    doc = encode(raw)
    parsed = parse_bus_document(doc)
    assert parsed.message_id == BUS_ID
    assert parsed.writer == WRITER
    assert parsed.intended_recipient == RECIPIENT
    assert parsed.body.encode() == raw
    recovered = decode(doc)
    assert recovered.canonical_message_bytes == raw
    assert recovered.semantic_message_id == "packet/../\u202ehidden"
    assert recovered.bus_message_id != recovered.semantic_message_id
    assert recovered.operation_id == "op-001"
    assert recovered.writer == WRITER
    assert recovered.actor == "agent:alpha"  # Claim only; not transport identity.


def test_bounded_read_only_scope_rejects_effects_and_privacy():
    for overrides in [
        {"effect_class": "PROTECTED_MUTATION"},
        {"security_profile": "PRIVATE"},
        {"security_profile": "SEALED"},
        {"performative": "EXECUTE"},
        {"performative": "CANCEL"},
        {"observed_at": None},
    ]:
        with pytest.raises(IntranelBusCarriageError):
            encode(semantic(**overrides))


def test_writer_and_target_require_external_expected_binding():
    doc = encode()
    for mismatch in [
        {"expected_writer": "Imposter"},
        {"expected_lane_identity": "imposter"},
        {"expected_target": "agent:alpha"},
        {"expected_recipient": "imposter"},
    ]:
        args = dict(expected_lane_identity=LANE, expected_writer=WRITER,
                    expected_recipient=RECIPIENT, expected_target=TARGET)
        args.update(mismatch)
        with pytest.raises(IntranelBusCarriageError):
            decode_intranel_bus_document(doc, **args)


def test_payload_digest_wrapper_and_packet_identity_mismatch_hold():
    doc = encode()
    for before, after in [
        (b"status", b"DELETE"),
        (b"semantic_sha256: ", b"semantic_sha256: " + b"0" * 64 + b"\n#"),
        (b"intranel_target: agent:beta", b"intranel_target: agent:alpha"),
    ]:
        tampered = doc.replace(before, after, 1)
        with pytest.raises(IntranelBusCarriageError):
            decode(tampered)


def test_conflicting_or_noncanonical_semantic_data_held():
    with pytest.raises(IntranelBusCarriageError):
        encode(b'{"protocol":"INTRANEL/1","protocol":"INTRANEL/1"}')
    with pytest.raises(IntranelBusCarriageError):
        encode(b'{"x":1.0}')
    with pytest.raises(IntranelBusCarriageError):
        encode(semantic() + b"\n")
    with pytest.raises(IntranelBusCarriageError):
        encode(semantic(message_id="ok", payload={"x": 2 ** 60}))


def test_header_injection_and_bus_id_reuse_rejected():
    with pytest.raises(IntranelBusCarriageError):
        encode(writer="Agent-Alpha\nwriter: Attacker")
    with pytest.raises(IntranelBusCarriageError):
        encode(bus_message_id="unbound-ID")
    with pytest.raises(IntranelBusCarriageError):
        encode(bus_message_id="packet/../\u202ehidden")


def test_same_operation_new_bus_delivery_keeps_distinct_packet_ids():
    first = encode(semantic(message_id="packet-A"))
    second = encode_intranel_bus_document(
        semantic(message_id="packet-B"), lane_identity=LANE,
        writer=WRITER, bus_message_id="agent_alpha-bounded-002",
        intended_recipient=RECIPIENT,
    )
    assert decode(first).operation_id == decode(second).operation_id
    assert decode(first).semantic_message_id != decode(second).semantic_message_id
    assert decode(first).bus_message_id != decode(second).bus_message_id


def test_undeclared_headers_duplicate_headers_and_route_injection_hold():
    valid = encode()
    for mutated in [
        valid.replace(b"requires_ack: true", b"requires_ack: true\npriority: 0"),
        valid.replace(b"writer: Agent-Alpha", b"writer: Agent-Alpha\nwriter: Attacker"),
        valid.replace(b"intended_recipient: agent-beta", b"intended_recipient: attacker"),
    ]:
        with pytest.raises(IntranelBusCarriageError):
            decode(mutated)


def test_wrong_claimed_receipt_is_not_promoted_to_effect_truth():
    raw = semantic(performative="RECEIPT", receipt={"status": "effect_complete"},
                   exact_subject="commit:sha", operation_id="op-001")
    decoded = decode(encode(raw))
    assert decoded.canonical_message_bytes == raw
    assert decoded.operation_id == "op-001"
    # This codec deliberately emits no operation-admission or effect result.
    assert not hasattr(decoded, "effect_verified")


def test_malformed_utf8_lone_surrogate_and_overbound_body_held():
    with pytest.raises(IntranelBusCarriageError):
        encode(semantic().replace(b"status", b"\xff"))
    with pytest.raises(IntranelBusCarriageError):
        encode(b'{"payload":"\\ud800"}')
    with pytest.raises(IntranelBusCarriageError):
        encode(semantic(payload={"body": "a" * 70_000}))
    with pytest.raises(IntranelBusCarriageError):
        decode(encode() + b"x" * 70_000)


def test_untrusted_semantic_type_fails_without_typeerror_escape():
    with pytest.raises(IntranelBusCarriageError):
        encode(semantic(performative=["QUERY"]))


def test_typed_cross_field_violations_held_at_carriage_boundary():
    for changes in (
        {"priority": 999},
        {"capabilities": "all"},
        {"performative": "REVIEW", "exact_subject": None},
        {"performative": "RECEIPT", "exact_subject": None, "receipt": None},
        {"expected_response": "DELETE"},
        {"constraints": [{"not": "a string"}]},
    ):
        with pytest.raises(IntranelBusCarriageError):
            encode(semantic(**changes))


def test_read_only_request_carriage_never_becomes_execute_authority():
    request = semantic(performative="REQUEST", effect_class="READ_ONLY",
                       security_profile="OPEN", parent_message_id="coordinator-001")
    received = decode(encode(request))
    assert received.canonical_message_bytes == request
    assert received.operation_id == "op-001"
    assert not hasattr(received, "effect_authorized")
    for attempted_escalation in (
        {"effect_class": "REVERSIBLE_MUTATION"},
        {"effect_class": "PROTECTED_MUTATION"},
        {"security_profile": "PRIVATE"},
        {"security_profile": "SEALED"},
    ):
        with pytest.raises(IntranelBusCarriageError):
            encode(semantic(performative="REQUEST", **attempted_escalation))


def test_long_header_inputs_never_generate_undecodable_output():
    for override in (
        {"bus_message_id": LANE + "-" + "a" * 70_000},
        {"intended_recipient": "a" * 70_000},
    ):
        with pytest.raises(IntranelBusCarriageError):
            encode(**override)


def test_partial_or_unknown_wire_fields_are_not_a_canonical_intranel_packet():
    import json

    for update in (
        lambda item: item.pop("reply_to"),
        lambda item: item.pop("conversation_id"),
        lambda item: item.update({"unrecognized": "execution"}),
    ):
        raw = json.loads(semantic())
        update(raw)
        canonical = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        with pytest.raises(IntranelBusCarriageError):
            encode(canonical)
