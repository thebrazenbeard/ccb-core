import importlib.util


def test_workstation_relay_module_is_shipped():
    assert importlib.util.find_spec("radar.workstation_relay") is not None


def test_relay_envelope_canonicalizes_body_and_renders_exact_bus_binding():
    import hashlib
    import radar.workstation_relay as relay

    assert hasattr(relay, "RelayEnvelope")
    envelope = relay.RelayEnvelope.create(
        sender="ONE",
        recipient="TWO",
        message_id="one-two-canary-001",
        nonce="0123456789abcdef0123456789abcdef",
        source_bus_message_id="bus-message-001",
        source_bus_commit="a" * 40,
        body="BT2_CANARY: hello\r\nworld",
    )
    expected_body = "BT2_CANARY: hello\nworld"
    assert envelope.body == expected_body
    assert envelope.body_sha256 == hashlib.sha256(
        expected_body.encode("utf-8")
    ).hexdigest()
    rendered = envelope.render()
    assert rendered.startswith("BT2_WORKSTATION_RELAY_V1\n")
    assert "sender: ONE\n" in rendered
    assert "recipient: TWO\n" in rendered
    assert "source_bus_commit: " + ("a" * 40) in rendered
    assert "requires_ack: true\n" in rendered
    assert "content_class: CANARY\n" in rendered
    assert rendered.endswith("---\n" + expected_body)


def test_verified_canary_follows_full_visible_text_state_machine():
    import radar.workstation_relay as relay

    required = (
        "TargetDescriptor",
        "TargetSnapshot",
        "RelayStore",
        "WorkstationRelay",
        "render_ack",
    )
    assert all(hasattr(relay, name) for name in required)

    envelope = relay.RelayEnvelope.create(
        sender="ONE",
        recipient="TWO",
        message_id="one-two-canary-002",
        nonce="abcdef0123456789abcdef0123456789",
        source_bus_message_id="bus-message-002",
        source_bus_commit="b" * 40,
        body="BT2_CANARY: relay semantics",
    )
    descriptor = relay.TargetDescriptor(
        recipient="TWO",
        normalized_url_path="/c/target-two",
        visible_identity="Two — Build Team Two",
    )
    snapshot = relay.TargetSnapshot(
        window_handle=41,
        normalized_url_path="/c/target-two",
        visible_identity="Two — Build Team Two",
    )

    class FakeTarget:
        def __init__(self):
            self.submits = 0
            self.written = None

        def discover(self, expected):
            assert expected == descriptor
            return (snapshot,)

        def activate(self, window_handle):
            assert window_handle == 41

        def snapshot(self, window_handle):
            assert window_handle == 41
            return snapshot

        def populate(self, window_handle, text):
            assert window_handle == 41
            self.written = text

        def submit(self, window_handle):
            assert window_handle == 41
            self.submits += 1

        def read_rendered(self, window_handle, message_id):
            assert window_handle == 41
            assert message_id == envelope.message_id
            return self.written

        def wait_for_ack(self, window_handle, message_id, timeout_seconds):
            assert window_handle == 41
            assert message_id == envelope.message_id
            assert timeout_seconds == 5.0
            return relay.render_ack(envelope, status="RECEIVED_VERIFIED")

    target = FakeTarget()
    source_checks = []

    engine = relay.WorkstationRelay(
        relay.RelayStore(":memory:"),
        source_bus_verifier=lambda candidate: (
            source_checks.append(candidate.source_bus_message_id) or True
        ),
    )
    receipt = engine.run(
        envelope,
        descriptor=descriptor,
        target=target,
        ack_timeout_seconds=5.0,
    )

    assert receipt.final_state == "ACK_VERIFIED"
    assert receipt.transitions == (
        "CREATED",
        "BUS_BOUND",
        "TARGET_DISCOVERED",
        "TARGET_VERIFIED_PREWRITE",
        "COMPOSER_POPULATED",
        "TARGET_REVERIFIED_PRESUBMIT",
        "SUBMITTED",
        "RENDERED_READBACK_VERIFIED",
        "ACK_PENDING",
        "ACK_VERIFIED",
    )
    assert receipt.side_effect_beyond_visible_text is False
    assert receipt.target_window_handle == 41
    assert receipt.prewrite_selector_digest == receipt.presubmit_selector_digest
    assert target.submits == 1
    assert source_checks == ["bus-message-002"]
