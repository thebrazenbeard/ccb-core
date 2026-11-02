from __future__ import annotations

import threading

from radar.dlq import DeadLetterStore
from radar.registry import IdentityRegistry
from radar.runtime import RadarRuntime
from radar.telemetry import TelemetryStore


LEASE_END_MS = 1_000_000


def _registry() -> IdentityRegistry:
    registry = IdentityRegistry()
    for identity in ("one", "two"):
        registry.register_identity(identity, display_name=identity.title())
        node_id = f"{identity}-node"
        registry.register_node(
            node_id,
            identity_id=identity,
            last_heartbeat_ms=0,
            lease_expires_ms=LEASE_END_MS,
        )
        registry.subscribe(node_id, domain="database", min_priority=4)
        registry.subscribe(node_id, domain="system", min_priority=4)
    return registry


def _raw(
    message_id: str,
    *,
    audience=("one",),
    domain="database",
    intent="event",
    priority=3,
    payload=None,
):
    return {
        "schema_version": 1,
        "message_id": message_id,
        "created_at": "2026-09-05T21:20:00Z",
        "sender": "radar",
        "audience": list(audience),
        "domain": domain,
        "intent": intent,
        "priority": priority,
        "payload": {"value": 1} if payload is None else payload,
    }


def _runtime(tmp_path, *, queue_size=8) -> RadarRuntime:
    return RadarRuntime(
        _registry(),
        telemetry=TelemetryStore(tmp_path / "telemetry.sqlite"),
        dlq=DeadLetterStore(tmp_path / "dlq.sqlite"),
        queue_size=queue_size,
    )


def test_unknown_taxonomy_is_durably_dlq_accounted(tmp_path):
    runtime = _runtime(tmp_path)

    receipt = runtime.admit(
        _raw("radar-unknown-0001", domain="not-a-domain"),
        now_ms=1_000,
        source_locator="bus/radar-v2@abc:messages/radar-unknown-0001.md",
    )

    assert receipt.state == "DLQ"
    assert receipt.message_id == "radar-unknown-0001"
    assert receipt.dlq_item_id is not None
    item = runtime.dlq.inspect(receipt.dlq_item_id)
    assert item.failure_stage == "ADMISSION"
    assert item.failure_code == "UNKNOWN_TAXONOMY"
    assert item.reason_category == "TAXONOMY"
    assert item.source_locator == "bus/radar-v2@abc:messages/radar-unknown-0001.md"
    assert runtime.telemetry.snapshot().as_dict() == {
        "routed": 0,
        "dropped": 0,
        "dlq": 1,
    }


def test_unknown_taxonomy_with_unserializable_payload_still_reaches_durable_dlq(tmp_path):
    runtime = _runtime(tmp_path)
    opaque_payload = object()

    receipt = runtime.admit(
        _raw("radar-unknown-opaque-0001", domain="not-a-domain", payload=opaque_payload),
        now_ms=1_500,
        source_locator="bus/radar-v2@def:messages/radar-unknown-opaque-0001.md",
    )

    assert receipt.state == "DLQ"
    assert receipt.dlq_item_id is not None
    item = runtime.dlq.inspect(receipt.dlq_item_id)
    assert item.message_id == "radar-unknown-opaque-0001"
    assert item.failure_code == "UNKNOWN_TAXONOMY"
    assert item.envelope["payload"] == "<object>"
    assert item.envelope["_radar_dlq_sanitization"] == {
        "schema": "RADAR_DLQ_SANITIZATION_V1",
        "changes": [{"path": "/payload", "reason": "OPAQUE_TYPE"}],
    }
    assert runtime.telemetry.snapshot().dlq == 1


def test_unknown_taxonomy_with_cyclic_payload_is_bounded_and_durably_recorded(tmp_path):
    runtime = _runtime(tmp_path)
    cyclic = {}
    cyclic["itself"] = cyclic

    receipt = runtime.admit(
        _raw("radar-cyclic-0001", domain="not-a-domain", payload=cyclic),
        now_ms=1_510,
    )

    assert receipt.state == "DLQ"
    assert receipt.dlq_item_id is not None
    item = runtime.dlq.inspect(receipt.dlq_item_id)
    assert item.envelope["payload"] == {"itself": "<cycle>"}
    assert item.envelope["_radar_dlq_sanitization"]["changes"] == [
        {"path": "/payload/itself", "reason": "CYCLE"}
    ]


def test_unknown_taxonomy_with_excessive_nesting_does_not_crash_admission(tmp_path):
    runtime = _runtime(tmp_path)
    payload = {"leaf": True}
    for _ in range(1_100):
        payload = {"next": payload}

    receipt = runtime.admit(
        _raw("radar-deep-0001", domain="not-a-domain", payload=payload),
        now_ms=1_520,
    )

    assert receipt.state == "DLQ"
    assert receipt.dlq_item_id is not None
    item = runtime.dlq.inspect(receipt.dlq_item_id)
    node = item.envelope["payload"]
    for _ in range(24):
        node = node["next"]
    assert node == "<depth-limit>"
    assert item.envelope["_radar_dlq_sanitization"]["changes"] == [
        {"path": "/payload" + "/next" * 24, "reason": "DEPTH_LIMIT"}
    ]


def test_unknown_taxonomy_does_not_silently_collapse_non_string_mapping_keys(tmp_path):
    runtime = _runtime(tmp_path)
    collision = {1: "first", "1": "second"}

    receipt = runtime.admit(
        _raw("radar-key-collision-0001", domain="not-a-domain", payload=collision),
        now_ms=1_530,
    )

    assert receipt.state == "DLQ"
    item = runtime.dlq.inspect(receipt.dlq_item_id)
    assert item.envelope["payload"] == "<invalid-map-keys>"
    assert item.envelope["_radar_dlq_sanitization"]["changes"] == [
        {"path": "/payload", "reason": "INVALID_MAP_KEYS"}
    ]


def test_literal_sanitizer_marker_is_preserved_without_claimed_transformation(tmp_path):
    runtime = _runtime(tmp_path)
    receipt = runtime.admit(
        _raw("radar-literal-marker-0001", domain="not-a-domain", payload="<width-limit>"),
        now_ms=1_535,
    )

    assert receipt.state == "DLQ"
    item = runtime.dlq.inspect(receipt.dlq_item_id)
    assert item.envelope["payload"] == "<width-limit>"
    assert "_radar_dlq_sanitization" not in item.envelope


def test_unknown_taxonomy_wide_payload_has_explicit_truncation_marker(tmp_path):
    runtime = _runtime(tmp_path)
    receipt = runtime.admit(
        _raw("radar-wide-0001", domain="not-a-domain", payload=list(range(1_000))),
        now_ms=1_540,
    )

    assert receipt.state == "DLQ"
    item = runtime.dlq.inspect(receipt.dlq_item_id)
    assert item.envelope["payload"] == "<width-limit>"
    assert item.envelope["_radar_dlq_sanitization"] == {
        "schema": "RADAR_DLQ_SANITIZATION_V1",
        "changes": [{"path": "/payload", "reason": "WIDTH_LIMIT"}],
    }


def test_unknown_taxonomy_aggregate_json_bytes_are_bounded(tmp_path):
    runtime = _runtime(tmp_path)
    # Width and individual strings are ordinary, but their aggregate is not.
    wide_text = {f"part-{i:03}": "x" * 1_024 for i in range(100)}
    receipt = runtime.admit(
        _raw("radar-byte-limit-0001", domain="not-a-domain", payload=wide_text),
        now_ms=1_550,
    )

    assert receipt.state == "DLQ"
    item = runtime.dlq.inspect(receipt.dlq_item_id)
    assert item.envelope == {
        "_radar_dlq_sanitization": {
            "schema": "RADAR_DLQ_SANITIZATION_V1",
            "changes": [{"path": "", "reason": "BYTE_LIMIT"}],
        },
    }


def test_reserved_sanitization_key_cannot_spoof_internal_metadata(tmp_path):
    runtime = _runtime(tmp_path)
    raw = _raw("radar-forged-meta-0001", domain="not-a-domain", payload="ordinary")
    raw["_radar_dlq_sanitization"] = {
        "schema": "RADAR_DLQ_SANITIZATION_V1",
        "changes": [],
    }

    receipt = runtime.admit(raw, now_ms=1_560)
    assert receipt.state == "DLQ"
    item = runtime.dlq.inspect(receipt.dlq_item_id)
    assert item.envelope == {
        "_radar_dlq_sanitization": {
            "schema": "RADAR_DLQ_SANITIZATION_V1",
            "changes": [{"path": "", "reason": "RESERVED_KEY"}],
        },
    }


def test_sanitization_sidecar_escapes_paths_and_bounds_event_count(tmp_path):
    runtime = _runtime(tmp_path)
    payload = {"a/b~c": object(), "items": [object() for _ in range(100)]}
    receipt = runtime.admit(
        _raw("radar-sidecar-0001", domain="not-a-domain", payload=payload),
        now_ms=1_570,
    )

    assert receipt.state == "DLQ"
    item = runtime.dlq.inspect(receipt.dlq_item_id)
    assert item.envelope["payload"]["a/b~c"] == "<object>"
    sidecar = item.envelope["_radar_dlq_sanitization"]
    assert sidecar["schema"] == "RADAR_DLQ_SANITIZATION_V1"
    assert sidecar["changes"][0] == {
        "path": "/payload/a~1b~0c", "reason": "OPAQUE_TYPE"
    }
    assert len(sidecar["changes"]) == 64
    assert sidecar["omitted_changes"] == 37


def test_dedupe_uses_payload_hash_across_route_context(tmp_path):
    runtime = _runtime(tmp_path)
    payload = {"same": "payload"}

    first = runtime.admit(
        _raw("radar-route-0001", audience=("one",), payload=payload), now_ms=10_000
    )
    duplicate = runtime.admit(
        _raw("radar-route-0002", audience=("one",), payload=payload), now_ms=10_100
    )
    other_route = runtime.admit(
        _raw("radar-route-0003", audience=("two",), payload=payload), now_ms=10_200
    )

    assert first.state == "QUEUED"
    assert duplicate.state == "DROPPED_DUPLICATE"
    assert other_route.state == "DROPPED_DUPLICATE"
    assert runtime.telemetry.snapshot().dropped == 2


def test_failed_queue_admission_does_not_poison_dedupe_retry(tmp_path):
    runtime = _runtime(tmp_path, queue_size=1)

    assert runtime.admit(
        _raw("radar-blocker-0001", payload={"blocker": True}),
        now_ms=15_000,
    ).state == "QUEUED"

    rejected = runtime.admit(
        _raw("radar-retry-0001", payload={"retry": True}),
        now_ms=15_100,
    )
    assert rejected.state == "DROPPED_QUEUE_FULL"

    assert runtime.dispatch_next(now_ms=15_150).state == "DISPATCHED"

    retry = runtime.admit(
        _raw("radar-retry-0002", payload={"retry": True}),
        now_ms=15_200,
    )
    assert retry.state == "QUEUED"
    assert runtime.telemetry.snapshot().as_dict() == {
        "routed": 1,
        "dropped": 1,
        "dlq": 0,
    }


def test_concurrent_retry_cannot_observe_uncommitted_dedupe_reservation(tmp_path):
    telemetry = TelemetryStore(
        tmp_path / "threaded-telemetry.sqlite",
        check_same_thread=False,
    )
    runtime = RadarRuntime(
        _registry(),
        telemetry=telemetry,
        dlq=DeadLetterStore(tmp_path / "threaded-dlq.sqlite"),
        queue_size=1,
    )
    assert runtime.admit(
        _raw("radar-race-blocker-0001", payload={"blocker": True}),
        now_ms=16_000,
    ).state == "QUEUED"

    real_queue = runtime.queue
    first_reached_queue = threading.Event()
    release_first = threading.Event()

    class PausingQueue:
        def push(self, envelope):
            if envelope.message_id == "radar-race-0001":
                first_reached_queue.set()
                assert release_first.wait(5), "timed out waiting to release first admission"
            return real_queue.push(envelope)

        def pop(self):
            return real_queue.pop()

    runtime.queue = PausingQueue()
    receipts = {}
    retry_done = threading.Event()

    def admit_first():
        receipts["first"] = runtime.admit(
            _raw("radar-race-0001", payload={"retry": True}),
            now_ms=16_100,
        )

    def admit_retry():
        try:
            receipts["retry"] = runtime.admit(
                _raw("radar-race-0002", payload={"retry": True}),
                now_ms=16_200,
            )
        finally:
            retry_done.set()

    first_thread = threading.Thread(target=admit_first)
    retry_thread = threading.Thread(target=admit_retry)
    first_thread.start()
    assert first_reached_queue.wait(5), "first admission did not reach queue"
    retry_thread.start()

    retry_finished_before_first_rollback = retry_done.wait(0.25)
    release_first.set()
    first_thread.join(5)
    retry_thread.join(5)

    assert not first_thread.is_alive()
    assert not retry_thread.is_alive()
    assert retry_finished_before_first_rollback is False
    assert receipts["first"].state == "DROPPED_QUEUE_FULL"
    assert receipts["retry"].state == "DROPPED_QUEUE_FULL"
    assert telemetry.snapshot().as_dict() == {
        "routed": 0,
        "dropped": 2,
        "dlq": 0,
    }


def test_priority_zero_dispatch_is_atomic_against_external_dispatch(tmp_path):
    telemetry = TelemetryStore(
        tmp_path / "priority-threaded-telemetry.sqlite",
        check_same_thread=False,
    )
    runtime = RadarRuntime(
        _registry(),
        telemetry=telemetry,
        dlq=DeadLetterStore(tmp_path / "priority-threaded-dlq.sqlite"),
        queue_size=2,
    )
    assert runtime.admit(
        _raw(
            "radar-priority-bg-0001",
            domain="system",
            intent="telemetry",
            priority=4,
            payload={"background": True},
        ),
        now_ms=17_000,
    ).state == "QUEUED"

    real_queue = runtime.queue
    urgent_reached_queue = threading.Event()
    release_urgent = threading.Event()

    class PausingQueue:
        def push(self, envelope):
            evicted = real_queue.push(envelope)
            if envelope.message_id == "radar-priority-urgent-0001":
                urgent_reached_queue.set()
                assert release_urgent.wait(5), "timed out waiting to release urgent admission"
            return evicted

        def pop(self):
            return real_queue.pop()

    runtime.queue = PausingQueue()
    receipts = {}
    external_done = threading.Event()

    def admit_urgent():
        receipts["urgent"] = runtime.admit(
            _raw(
                "radar-priority-urgent-0001",
                domain="system",
                intent="command",
                priority=0,
                payload={"halt": True},
            ),
            now_ms=17_600,
        )

    def dispatch_external():
        try:
            receipts["external"] = runtime.dispatch_next(now_ms=17_650)
        finally:
            external_done.set()

    urgent_thread = threading.Thread(target=admit_urgent)
    external_thread = threading.Thread(target=dispatch_external)
    urgent_thread.start()
    assert urgent_reached_queue.wait(5), "urgent admission did not reach queue"
    external_thread.start()

    external_finished_before_urgent_dispatch = external_done.wait(0.25)
    release_urgent.set()
    urgent_thread.join(5)
    external_thread.join(5)

    assert not urgent_thread.is_alive()
    assert not external_thread.is_alive()
    assert external_finished_before_urgent_dispatch is False
    assert receipts["urgent"].state == "DISPATCHED"
    assert receipts["urgent"].message_id == "radar-priority-urgent-0001"
    assert receipts["external"].state == "DISPATCHED"
    assert receipts["external"].message_id == "radar-priority-bg-0001"
    assert telemetry.snapshot().as_dict() == {
        "routed": 2,
        "dropped": 0,
        "dlq": 0,
    }


def test_priority_zero_evicts_background_counts_drop_and_dispatches_immediately(tmp_path):
    runtime = _runtime(tmp_path, queue_size=2)

    assert runtime.admit(
        _raw("radar-bg-0001", priority=4, domain="system", intent="telemetry"),
        now_ms=20_000,
    ).state == "QUEUED"
    assert runtime.admit(
        _raw("radar-bg-0002", priority=4, domain="system", intent="telemetry", payload={"n": 2}),
        now_ms=20_600,
    ).state == "QUEUED"

    urgent = runtime.admit(
        _raw("radar-halt-0001", priority=0, domain="system", intent="command", payload={"halt": True}),
        now_ms=21_200,
    )

    assert urgent.state == "DISPATCHED"
    assert urgent.recipients == ("one-node",)
    assert urgent.evicted_message_id == "radar-bg-0002"
    assert runtime.telemetry.snapshot().as_dict() == {
        "routed": 1,
        "dropped": 1,
        "dlq": 0,
    }


def test_normal_dispatch_accounts_one_routed_message_not_recipient_fanout(tmp_path):
    runtime = _runtime(tmp_path)
    queued = runtime.admit(
        _raw("radar-fanout-0001", audience=(), payload={"fanout": True}),
        now_ms=30_000,
    )
    assert queued.state == "QUEUED"

    dispatched = runtime.dispatch_next(now_ms=30_100)

    assert dispatched.state == "DISPATCHED"
    assert dispatched.recipients == ("one-node", "two-node")
    assert runtime.telemetry.snapshot().routed == 1
