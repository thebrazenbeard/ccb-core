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
