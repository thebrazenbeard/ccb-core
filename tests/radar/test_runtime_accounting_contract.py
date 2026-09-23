from __future__ import annotations

from radar.dlq import DeadLetterStore
from radar.registry import IdentityRegistry
from radar.runtime import RadarRuntime
from radar.telemetry import TelemetryStore


def _raw(message_id: str, *, audience=(), payload=None) -> dict[str, object]:
    return {
        "schema_version": 1,
        "message_id": message_id,
        "created_at": "2026-09-06T14:30:00Z",
        "sender": "radar",
        "audience": list(audience),
        "domain": "database",
        "intent": "event",
        "priority": 3,
        "payload": {"message_id": message_id} if payload is None else payload,
    }


def _runtime(tmp_path, *, subscribed: bool) -> RadarRuntime:
    registry = IdentityRegistry()
    for identity in ("one", "two"):
        registry.register_identity(identity, display_name=identity.title())
        node_id = f"{identity}-node"
        registry.register_node(
            node_id,
            identity_id=identity,
            last_heartbeat_ms=0,
            lease_expires_ms=10_000,
        )
        if subscribed:
            registry.subscribe(node_id, domain="database", intent="event", min_priority=4)
    return RadarRuntime(
        registry,
        telemetry=TelemetryStore(tmp_path / "telemetry.sqlite"),
        dlq=DeadLetterStore(tmp_path / "dlq.sqlite"),
    )


def test_queue_admission_does_not_count_as_routed(tmp_path):
    runtime = _runtime(tmp_path, subscribed=True)

    receipt = runtime.admit(_raw("radar-accounting-0001", audience=("one",)), now_ms=1_000)

    assert receipt.state == "QUEUED"
    assert runtime.telemetry.snapshot().as_dict() == {
        "routed": 0,
        "dropped": 0,
        "dlq": 0,
    }


def test_no_route_dispatch_counts_drop_without_counting_routed(tmp_path):
    runtime = _runtime(tmp_path, subscribed=False)
    assert runtime.admit(_raw("radar-accounting-0002", audience=("one",)), now_ms=2_000).state == "QUEUED"

    receipt = runtime.dispatch_next(now_ms=2_100)

    assert receipt.state == "DROPPED_NO_ROUTE"
    assert runtime.telemetry.snapshot().as_dict() == {
        "routed": 0,
        "dropped": 1,
        "dlq": 0,
    }


def test_fanout_dispatch_counts_one_routed_message_not_recipient_count(tmp_path):
    runtime = _runtime(tmp_path, subscribed=True)
    assert runtime.admit(_raw("radar-accounting-0003", payload={"fanout": True}), now_ms=3_000).state == "QUEUED"

    receipt = runtime.dispatch_next(now_ms=3_100)

    assert receipt.state == "DISPATCHED"
    assert receipt.recipients == ("one-node", "two-node")
    assert runtime.telemetry.snapshot().as_dict() == {
        "routed": 1,
        "dropped": 0,
        "dlq": 0,
    }
