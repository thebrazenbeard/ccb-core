import importlib.util

import pytest


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

        def activate(self, window_handle, target_token=None):
            assert window_handle == 41

        def snapshot(self, window_handle, target_token=None):
            assert window_handle == 41
            return snapshot

        def populate(self, window_handle, target_token, text):
            assert window_handle == 41
            self.written = text

        def submit(self, window_handle, target_token=None):
            assert window_handle == 41
            self.submits += 1

        def read_rendered(self, window_handle, target_token, message_id):
            assert window_handle == 41
            assert message_id == envelope.message_id
            return self.written

        def wait_for_ack(self, window_handle, target_token, message_id, timeout_seconds):
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


class ScriptedTarget:
    def __init__(
        self,
        *,
        envelope,
        snapshots,
        discoveries=None,
        rendered=True,
        ack="valid",
        fail_populate=False,
        fail_submit=False,
    ):
        self.envelope = envelope
        self.snapshots = list(snapshots)
        self.discoveries = (
            tuple(discoveries)
            if discoveries is not None
            else (self.snapshots[0],)
        )
        self.rendered = rendered
        self.ack = ack
        self.fail_populate = fail_populate
        self.fail_submit = fail_submit
        self.submits = 0
        self.written = None
        self.activated = []

    def discover(self, expected):
        return self.discoveries

    def activate(self, window_handle, target_token=None):
        self.activated.append((window_handle, target_token))

    def snapshot(self, window_handle, target_token=None):
        if not self.snapshots:
            raise RuntimeError("target disappeared")
        return self.snapshots.pop(0)

    def populate(self, window_handle, target_token, text):
        if self.fail_populate:
            raise RuntimeError("write failed")
        self.written = text

    def submit(self, window_handle, target_token=None):
        if self.fail_submit:
            raise RuntimeError("submit failed")
        self.submits += 1

    def read_rendered(self, window_handle, target_token, message_id):
        return self.written if self.rendered else None

    def wait_for_ack(self, window_handle, target_token, message_id, timeout_seconds):
        import radar.workstation_relay as relay
        if self.ack == "valid":
            return relay.render_ack(self.envelope, status="RECEIVED_VERIFIED")
        if self.ack == "hold":
            return relay.render_ack(self.envelope, status="HOLD_SOURCE_UNVERIFIED")
        if self.ack == "wrong-nonce":
            return relay.render_ack(
                self.envelope, status="RECEIVED_VERIFIED"
            ).replace(self.envelope.nonce, "0" * 32)
        if self.ack == "wrong-digest":
            return relay.render_ack(
                self.envelope, status="RECEIVED_VERIFIED"
            ).replace(self.envelope.body_sha256, "0" * 64)
        return None


def relay_fixture(message_id="one-two-edge-001"):
    import radar.workstation_relay as relay
    envelope = relay.RelayEnvelope.create(
        sender="ONE",
        recipient="TWO",
        message_id=message_id,
        nonce="1234567890abcdef1234567890abcdef",
        source_bus_message_id="bus-edge-001",
        source_bus_commit="c" * 40,
        body="BT2_CANARY: inert edge probe",
    )
    descriptor = relay.TargetDescriptor(
        recipient="TWO",
        normalized_url_path="/c/two",
        visible_identity="Two — Build Team Two",
    )
    snapshot = relay.TargetSnapshot(
        window_handle=99,
        normalized_url_path="/c/two",
        visible_identity="Two — Build Team Two",
    )
    return relay, envelope, descriptor, snapshot


def test_target_ambiguity_fails_before_composer_write():
    relay, envelope, descriptor, snapshot = relay_fixture("edge-ambiguous")
    other = relay.TargetSnapshot(
        window_handle=100,
        normalized_url_path=snapshot.normalized_url_path,
        visible_identity=snapshot.visible_identity,
    )
    target = ScriptedTarget(
        envelope=envelope,
        snapshots=[snapshot],
        discoveries=(snapshot, other),
    )
    result = relay.WorkstationRelay(
        relay.RelayStore(":memory:"),
        source_bus_verifier=lambda _: True,
    ).run(envelope, descriptor=descriptor, target=target, ack_timeout_seconds=1)
    assert result.final_state == "TARGET_AMBIGUOUS"
    assert target.written is None
    assert target.submits == 0


def test_target_drift_after_write_aborts_without_submit():
    relay, envelope, descriptor, snapshot = relay_fixture("edge-drift")
    drifted = relay.TargetSnapshot(
        window_handle=99,
        normalized_url_path="/c/someone-else",
        visible_identity="Someone Else",
    )
    target = ScriptedTarget(
        envelope=envelope,
        snapshots=[snapshot, drifted],
    )
    result = relay.WorkstationRelay(
        relay.RelayStore(":memory:"),
        source_bus_verifier=lambda _: True,
    ).run(envelope, descriptor=descriptor, target=target, ack_timeout_seconds=1)
    assert result.final_state == "TARGET_CHANGED_PRE_SUBMIT"
    assert target.written is not None
    assert target.submits == 0


def test_target_disappearing_before_submit_fails_closed():
    relay, envelope, descriptor, snapshot = relay_fixture("edge-disappears")
    target = ScriptedTarget(envelope=envelope, snapshots=[snapshot])
    result = relay.WorkstationRelay(
        relay.RelayStore(":memory:"),
        source_bus_verifier=lambda _: True,
    ).run(envelope, descriptor=descriptor, target=target, ack_timeout_seconds=1)
    assert result.final_state == "TARGET_CHANGED_PRE_SUBMIT"
    assert target.submits == 0


def test_missing_rendered_readback_is_submitted_unverified_and_not_replayed():
    relay, envelope, descriptor, snapshot = relay_fixture("edge-uncertain")
    store = relay.RelayStore(":memory:")
    first_target = ScriptedTarget(
        envelope=envelope,
        snapshots=[snapshot, snapshot],
        rendered=False,
    )
    engine = relay.WorkstationRelay(store, source_bus_verifier=lambda _: True)
    first = engine.run(
        envelope, descriptor=descriptor, target=first_target,
        ack_timeout_seconds=1,
    )
    assert first.final_state == "SUBMITTED_UNVERIFIED"
    assert first_target.submits == 1

    second_target = ScriptedTarget(
        envelope=envelope,
        snapshots=[snapshot, snapshot],
    )
    second = engine.run(
        envelope, descriptor=descriptor, target=second_target,
        ack_timeout_seconds=1,
    )
    assert second == first
    assert second_target.submits == 0


@pytest.mark.parametrize(
    ("ack_mode", "expected"),
    [
        (None, "ACK_TIMEOUT"),
        ("wrong-nonce", "ACK_MISMATCH"),
        ("wrong-digest", "ACK_MISMATCH"),
        ("hold", "SOURCE_BUS_UNVERIFIED"),
    ],
)
def test_ack_failure_modes_do_not_promote_delivery(ack_mode, expected):
    relay, envelope, descriptor, snapshot = relay_fixture(
        "edge-ack-" + str(ack_mode)
    )
    target = ScriptedTarget(
        envelope=envelope,
        snapshots=[snapshot, snapshot],
        ack=ack_mode,
    )
    result = relay.WorkstationRelay(
        relay.RelayStore(":memory:"),
        source_bus_verifier=lambda _: True,
    ).run(envelope, descriptor=descriptor, target=target, ack_timeout_seconds=1)
    assert result.final_state == expected


def test_unverified_bus_source_never_discovers_or_writes_target():
    relay, envelope, descriptor, snapshot = relay_fixture("edge-source")
    target = ScriptedTarget(envelope=envelope, snapshots=[snapshot, snapshot])
    result = relay.WorkstationRelay(
        relay.RelayStore(":memory:"),
        source_bus_verifier=lambda _: False,
    ).run(envelope, descriptor=descriptor, target=target, ack_timeout_seconds=1)
    assert result.final_state == "SOURCE_BUS_UNVERIFIED"
    assert target.activated == []
    assert target.written is None


def test_target_recipient_mismatch_is_rejected_before_target_discovery():
    relay, envelope, _descriptor, snapshot = relay_fixture("edge-recipient")
    wrong = relay.TargetDescriptor(
        recipient="THREE",
        normalized_url_path="/c/three",
        visible_identity="Three — Build Team Two",
    )
    target = ScriptedTarget(envelope=envelope, snapshots=[snapshot, snapshot])
    with pytest.raises(relay.RelayError, match="TARGET_RECIPIENT_MISMATCH"):
        relay.WorkstationRelay(
            relay.RelayStore(":memory:"),
            source_bus_verifier=lambda _: True,
        ).run(envelope, descriptor=wrong, target=target, ack_timeout_seconds=1)
    assert target.activated == []


def test_receipt_contains_no_browser_auth_or_secret_fields():
    relay, envelope, descriptor, snapshot = relay_fixture("edge-secret-free")
    target = ScriptedTarget(
        envelope=envelope,
        snapshots=[snapshot, snapshot],
    )
    receipt = relay.WorkstationRelay(
        relay.RelayStore(":memory:"),
        source_bus_verifier=lambda _: True,
    ).run(envelope, descriptor=descriptor, target=target, ack_timeout_seconds=1)
    serialized = str(receipt.to_dict()).lower()
    for forbidden in ("cookie", "authorization", "bearer", "password", "profile_secret"):
        assert forbidden not in serialized


def test_canary_gate_rejects_command_like_or_unbounded_payloads():
    import radar.workstation_relay as relay

    bad_bodies = (
        "echo hello",
        "BT2_CANARY: sudo -n true",
        "BT2_CANARY: rm -rf temp",
        "BT2_CANARY: dir && whoami",
        "BT2_CANARY: powershell -Command Get-Process",
        "BT2_CANARY: " + ("x" * 600),
    )
    for index, body in enumerate(bad_bodies):
        with pytest.raises(relay.RelayError, match="CANARY_CONTENT_REJECTED"):
            relay.RelayEnvelope.create(
                sender="ONE",
                recipient="TWO",
                message_id=f"unsafe-{index}",
                nonce="abcdefabcdefabcdefabcdefabcdefab",
                source_bus_message_id="bus-unsafe",
                source_bus_commit="d" * 40,
                body=body,
            )


def test_target_snapshot_binds_exact_tab_token_and_engine_uses_it():
    import radar.workstation_relay as relay

    descriptor = relay.TargetDescriptor(
        recipient="TWO",
        normalized_url_path="/c/exact-tab",
        visible_identity="Two — Exact Chat",
    )
    snapshot = relay.TargetSnapshot(
        window_handle=501,
        target_token="firefox-tab-7",
        normalized_url_path="/c/exact-tab",
        visible_identity="Two — Exact Chat",
    )
    envelope = relay.RelayEnvelope.create(
        sender="ONE",
        recipient="TWO",
        message_id="exact-tab-canary",
        nonce="fedcba9876543210fedcba9876543210",
        source_bus_message_id="bus-exact-tab",
        source_bus_commit="e" * 40,
        body="BT2_CANARY: exact tab binding",
    )

    class ExactTabTarget:
        def __init__(self):
            self.calls = []
            self.written = None

        def discover(self, expected):
            return (snapshot,)

        def activate(self, window_handle, target_token):
            self.calls.append(("activate", window_handle, target_token))

        def snapshot(self, window_handle, target_token):
            self.calls.append(("snapshot", window_handle, target_token))
            return snapshot

        def populate(self, window_handle, target_token, text):
            self.calls.append(("populate", window_handle, target_token))
            self.written = text

        def submit(self, window_handle, target_token):
            self.calls.append(("submit", window_handle, target_token))

        def read_rendered(self, window_handle, target_token, message_id):
            return self.written

        def wait_for_ack(
            self, window_handle, target_token, message_id, timeout_seconds
        ):
            return relay.render_ack(envelope, status="RECEIVED_VERIFIED")

    target = ExactTabTarget()
    receipt = relay.WorkstationRelay(
        relay.RelayStore(":memory:"),
        source_bus_verifier=lambda _: True,
    ).run(envelope, descriptor=descriptor, target=target, ack_timeout_seconds=1)

    assert receipt.final_state == "ACK_VERIFIED"
    assert receipt.target_window_handle == 501
    assert receipt.target_token == "firefox-tab-7"
    assert ("activate", 501, "firefox-tab-7") in target.calls
    assert ("submit", 501, "firefox-tab-7") in target.calls
