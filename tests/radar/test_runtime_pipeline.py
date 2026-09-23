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
    assert runtime.telemetry.snapshot().dlq == 1


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
