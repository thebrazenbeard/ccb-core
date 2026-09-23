from __future__ import annotations

from radar.dlq import DeadLetterStore
from radar.envelope import RadarEnvelope
from radar.registry import IdentityRegistry
from radar.runtime import RadarRuntime
from radar.telemetry import TelemetryStore


MIDDLEWARE_VECTOR_HASH = "78b837d32f7965dc946e9c196293310b7f0b50be07fc67b7e6407a7a53b81ba3"


def _runtime(tmp_path) -> RadarRuntime:
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
        registry.subscribe(node_id, domain="database", intent="event", min_priority=4)
    return RadarRuntime(
        registry,
        telemetry=TelemetryStore(tmp_path / "telemetry.sqlite"),
        dlq=DeadLetterStore(tmp_path / "dlq.sqlite"),
    )


def _message(message_id: str, audience: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "message_id": message_id,
        "created_at": "2026-09-06T14:25:00Z",
        "sender": "radar",
        "audience": [audience],
        "domain": "database",
        "intent": "event",
        "priority": 3,
        "payload": {"same": "canonical-payload"},
    }


def test_envelope_idempotency_key_matches_original_middleware_hash_bytes():
    raw = _message("radar-dedupe-vector-0001", "one")
    raw["payload"] = {"user_id": "u_9918", "token_status": "expired"}
    result = RadarEnvelope.from_mapping(raw)
    assert result.envelope is not None

    assert result.envelope.idempotency_key == MIDDLEWARE_VECTOR_HASH


def test_runtime_uses_middleware_idempotency_key_exactly(tmp_path):
    runtime = _runtime(tmp_path)
    result = RadarEnvelope.from_mapping(_message("radar-dedupe-key-0001", "one"))
    assert result.envelope is not None

    assert runtime._dedupe_key(result.envelope) == result.envelope.idempotency_key


def test_same_payload_hash_dedupes_across_route_context_inside_window(tmp_path):
    runtime = _runtime(tmp_path)

    first = runtime.admit(_message("radar-dedupe-0001", "one"), now_ms=1_000)
    second = runtime.admit(_message("radar-dedupe-0002", "two"), now_ms=1_200)

    assert first.state == "QUEUED"
    assert second.state == "DROPPED_DUPLICATE"
    assert runtime.telemetry.snapshot().dropped == 1


def test_same_payload_hash_is_admitted_again_outside_500ms_window(tmp_path):
    runtime = _runtime(tmp_path)

    first = runtime.admit(_message("radar-dedupe-0011", "one"), now_ms=2_000)
    later = runtime.admit(_message("radar-dedupe-0012", "two"), now_ms=2_501)

    assert first.state == "QUEUED"
    assert later.state == "QUEUED"
    assert runtime.telemetry.snapshot().dropped == 0
