"""Source-stage INTRANEL/1 carriage across the actual Bus writer-lane grammar."""

import json
import hashlib

import pytest

from radar.intranel_bus_document import (
    IntranelBusCarriageError,
    decode_intranel_bus_document,
    encode_intranel_bus_document,
)
from radar.message_grammar import parse_bus_document
from radar.safe_lane_append import (
    CommitReadback, CommittedMessage, ControlSnapshot, FileReadback,
    IdentityHistory, LaneBinding, SafeAppendError, SafeLaneAppender,
)


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


def decode(doc, *, prev=None):
    return decode_intranel_bus_document(
        doc,
        expected_lane_identity=LANE,
        expected_writer=WRITER,
        expected_recipient=RECIPIENT,
        expected_target=TARGET,
        expected_prev_bus_message_id=prev,
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


class FakeBoundedWriterLane:
    """In-memory object/ref backend; never calls GitHub or a protected Bus."""

    def __init__(self):
        self.branch = "bus/agent-alpha"
        self.head = "a" * 40
        self.blobs = {}
        self.trees = {}
        self.commits = {}
        self.files = {}
        self.history = []

    def read(self):
        return ControlSnapshot(
            repository="example/ccb-base", control_commit_sha="b" * 40,
            topology_digest="test-topology-digest",
            lanes={LANE: LaneBinding(identity=LANE, writer=WRITER, branch=self.branch)},
        )

    def read_identity(self, identity):
        assert identity == LANE
        return IdentityHistory(
            complete=True,
            tip_message_ids=(self.history[-1].message_id,) if self.history else (),
            messages=tuple(self.history),
        )

    def read_branch_head(self, branch):
        assert branch == self.branch
        return self.head

    def read_file_at_commit(self, commit_sha, path):
        return self.files.get((commit_sha, path))

    def read_commit(self, commit_sha):
        return self.commits.get(commit_sha)

    def create_blob(self, content):
        digest = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
        self.blobs[digest] = content
        return digest

    def create_tree(self, base_commit_sha, path, blob_sha):
        digest = hashlib.sha1((base_commit_sha + path + blob_sha).encode()).hexdigest()
        self.trees[digest] = (path, blob_sha)
        return digest

    def create_commit(self, message, tree_sha, parent_sha):
        digest = hashlib.sha1((message + tree_sha + parent_sha).encode()).hexdigest()
        path, blob_sha = self.trees[tree_sha]
        self.commits[digest] = CommitReadback(
            parent_shas=(parent_sha,), tree_sha=tree_sha, changed_paths=(path,),
        )
        self.files[(digest, path)] = FileReadback(
            blob_sha=blob_sha, content=self.blobs[blob_sha],
        )
        return digest

    def update_ref_non_force(self, branch, candidate_sha):
        assert branch == self.branch
        self.head = candidate_sha
        return True

    def record(self, receipt):
        self.history.append(CommittedMessage(
            message_id=receipt.message_id,
            message_sha256=receipt.message_sha256,
            commit_sha=receipt.commit_sha,
            path=receipt.path,
            blob_sha=receipt.message_blob_sha,
            prev_message_id=receipt.prev_message_id,
            branch=receipt.lane_ref,
        ))


def test_intranel_bus_document_crosses_safe_append_and_readback_twice():
    backend = FakeBoundedWriterLane()
    appender = SafeLaneAppender(
        control_source=backend, history_source=backend, git_backend=backend,
    )
    first_bytes = encode(semantic(message_id="intranel-first", operation_id="op-01"))
    original_head = backend.head
    first = appender.append_bus_message_v1(LANE, original_head, first_bytes)
    assert first.result == "APPENDED"
    assert first.readback_verified is True
    assert first.path == f"messages/{BUS_ID}.md"
    assert decode(backend.read_file_at_commit(first.commit_sha, first.path).content).semantic_message_id == "intranel-first"
    backend.record(first)  # Represent *independently reconstructed* committed history.

    incorrect_prev = encode_intranel_bus_document(
        semantic(message_id="intranel-bad-prev"),
        lane_identity=LANE, writer=WRITER,
        bus_message_id="agent_alpha-bad-prev", intended_recipient=RECIPIENT,
        prev_bus_message_id="agent_alpha-unrelated",
    )
    with pytest.raises(SafeAppendError, match="PREDECESSOR_MISMATCH"):
        appender.append_bus_message_v1(LANE, backend.head, incorrect_prev)

    # A reply is a new Bus writer-lane message, not the semantic parent packet.
    second_id = "agent_alpha-bounded-002"
    second_bytes = encode_intranel_bus_document(
        semantic(message_id="intranel-second", operation_id="op-02",
                 parent_message_id="intranel-first"),
        lane_identity=LANE, writer=WRITER, bus_message_id=second_id,
        intended_recipient=RECIPIENT, prev_bus_message_id=BUS_ID,
    )
    second = appender.append_bus_message_v1(LANE, backend.head, second_bytes)
    assert second.result == "APPENDED"
    assert second.prev_message_id == BUS_ID
    assert second.readback_verified is True
    assert decode(second_bytes, prev=BUS_ID).prev_bus_message_id == BUS_ID
    with pytest.raises(IntranelBusCarriageError):
        decode(second_bytes)  # No trusted predecessor supplied.
    backend.record(second)
    stale_third = encode_intranel_bus_document(
        semantic(message_id="intranel-stale"), lane_identity=LANE,
        writer=WRITER, bus_message_id="agent_alpha-bounded-003",
        intended_recipient=RECIPIENT, prev_bus_message_id=second_id,
    )
    with pytest.raises(SafeAppendError, match="STALE_LANE_HEAD"):
        appender.append_bus_message_v1(LANE, original_head, stale_third)

    # Repetition after head advances reconciles the original effect, but a
    # reused writer-local Bus ID with different semantics hard-collides.
    repeated = appender.append_bus_message_v1(LANE, first.expected_lane_head, first_bytes)
    assert repeated.result == "ALREADY_COMMITTED"
    assert repeated.readback_verified is True
    altered_bytes = encode(semantic(message_id="intranel-altered"))
    with pytest.raises(SafeAppendError, match="MESSAGE_ID_COLLISION"):
        appender.append_bus_message_v1(LANE, original_head, altered_bytes)
