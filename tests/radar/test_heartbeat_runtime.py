from __future__ import annotations

import threading

from radar.envelope import EnvelopeClassification, RadarEnvelope
from radar.heartbeat import HeartbeatRuntime, HeartbeatSinkError
from radar.telemetry import HEARTBEAT_INTERVAL_MS, TelemetryStore


def test_heartbeat_message_is_radar_background_telemetry(tmp_path):
    store = TelemetryStore(tmp_path / "telemetry.sqlite")
    store.increment("routed", 2)
    store.increment("dropped", 1)
    store.increment("dlq", 3)

    heartbeat = store.emit_heartbeat(1_000)

    assert heartbeat is not None
    assert heartbeat["sender"] == "radar"
    assert heartbeat["domain"] == "system"
    assert heartbeat["intent"] == "telemetry"
    assert heartbeat["priority"] == 4
    assert heartbeat["heartbeat_sequence"] == 1
    assert heartbeat["missed_intervals"] == 0
    assert heartbeat["counts"] == {"routed": 2, "dropped": 1, "dlq": 3}


def test_heartbeat_is_a_canonical_radar_envelope_not_an_out_of_band_shape(tmp_path):
    store = TelemetryStore(tmp_path / "telemetry.sqlite")
    store.increment("routed", 4)

    heartbeat = store.emit_heartbeat(
        1_000,
        source_ref="main@abc",
        provider_state="DEGRADED",
    )

    assert heartbeat is not None
    result = RadarEnvelope.from_mapping(heartbeat)
    assert result.classification is EnvelopeClassification.VALID
    assert result.envelope is not None
    assert result.envelope.message_id == "radar-heartbeat-1000"
    assert result.envelope.created_at == "1970-01-01T00:00:01Z"
    assert result.envelope.audience == ()
    assert result.envelope.priority == 4
    assert result.envelope.payload == {
        "heartbeat_sequence": 1,
        "emitted_at_ms": 1_000,
        "missed_intervals": 0,
        "counts": {"routed": 4, "dropped": 0, "dlq": 0},
        "source_ref": "main@abc",
        "provider_state": "DEGRADED",
    }


def test_runtime_resumes_from_persisted_due_time_without_sixty_second_lag(tmp_path):
    store = TelemetryStore(tmp_path / "telemetry.sqlite")
    assert store.emit_heartbeat(1_000) is not None

    clock = {"now_ms": 51_000}
    sleeps: list[float] = []
    emitted: list[dict[str, object]] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now_ms"] += round(seconds * 1_000)

    runtime = HeartbeatRuntime(
        store,
        sink=emitted.append,
        clock_ms=lambda: clock["now_ms"],
        sleep=sleep,
    )
    runtime.run_forever(should_stop=lambda: len(emitted) >= 1)

    assert sleeps == [10.0]
    assert len(emitted) == 1
    assert emitted[0]["emitted_at_ms"] == 1_000 + HEARTBEAT_INTERVAL_MS
    assert emitted[0]["heartbeat_sequence"] == 2
    assert emitted[0]["missed_intervals"] == 0


def test_runtime_emits_on_exact_sixty_second_cadence_and_stops_cleanly(tmp_path):
    store = TelemetryStore(tmp_path / "telemetry.sqlite")
    clock = {"now_ms": 0}
    emitted: list[dict[str, object]] = []
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now_ms"] += round(seconds * 1_000)

    runtime = HeartbeatRuntime(
        store,
        sink=emitted.append,
        clock_ms=lambda: clock["now_ms"],
        sleep=sleep,
        source_ref=lambda: "main@abc",
        provider_state=lambda: "DEGRADED",
    )
    runtime.run_forever(should_stop=lambda: len(emitted) >= 2)

    assert [item["emitted_at_ms"] for item in emitted] == [0, HEARTBEAT_INTERVAL_MS]
    assert [item["heartbeat_sequence"] for item in emitted] == [1, 2]
    assert [item["missed_intervals"] for item in emitted] == [0, 0]
    assert sleeps == [60.0]
    assert emitted[-1]["source_ref"] == "main@abc"
    assert emitted[-1]["provider_state"] == "DEGRADED"
    assert [record.delivery_state for record in store.recent_heartbeats()] == [
        "DELIVERED",
        "DELIVERED",
    ]


def test_duplicate_emitters_share_one_durable_interval_claim(tmp_path):
    path = tmp_path / "duplicate-emitters.sqlite"
    first = TelemetryStore(path, check_same_thread=False)
    second = TelemetryStore(path, check_same_thread=False)
    start = threading.Barrier(3)
    result_lock = threading.Lock()
    results = []
    errors = []

    def emit(store):
        try:
            start.wait()
            heartbeat = store.emit_heartbeat(10_000)
            with result_lock:
                results.append(heartbeat)
        except Exception as exc:
            with result_lock:
                errors.append(exc)

    threads = [
        threading.Thread(target=emit, args=(first,)),
        threading.Thread(target=emit, args=(second,)),
    ]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(5)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    emitted = [heartbeat for heartbeat in results if heartbeat is not None]
    assert len(emitted) == 1
    assert emitted[0]["message_id"] == "radar-heartbeat-10000"
    assert emitted[0]["heartbeat_sequence"] == 1
    assert emitted[0]["missed_intervals"] == 0
    rows = first.recent_heartbeats()
    assert len(rows) == 1
    assert rows[0].emitted_at_ms == 10_000
    assert rows[0].delivery_state == "DELIVERED"


def test_sink_failure_is_durable_and_scheduler_recovers_next_interval(tmp_path):
    store = TelemetryStore(tmp_path / "sink-failure.sqlite")
    clock = {"now_ms": 0}
    attempts = []
    successful = []
    sleeps = []

    def sink(message):
        attempts.append(message["message_id"])
        if len(attempts) == 1:
            raise RuntimeError("transient sink failure")
        successful.append(message["message_id"])

    def sleep(seconds):
        sleeps.append(seconds)
        clock["now_ms"] += round(seconds * 1_000)

    runtime = HeartbeatRuntime(
        store,
        sink=sink,
        clock_ms=lambda: clock["now_ms"],
        sleep=sleep,
    )
    runtime.run_forever(should_stop=lambda: len(successful) == 1)

    assert attempts == ["radar-heartbeat-0", f"radar-heartbeat-{HEARTBEAT_INTERVAL_MS}"]
    assert successful == [f"radar-heartbeat-{HEARTBEAT_INTERVAL_MS}"]
    assert sleeps == [60.0]
    records = store.recent_heartbeats()
    assert [record.delivery_state for record in records] == ["DELIVERED", "UNCONFIRMED"]
    assert [record.heartbeat_id for record in records] == [2, 1]


def test_tick_wraps_sink_failure_without_misclassifying_store_failure(tmp_path):
    store = TelemetryStore(tmp_path / "sink-error.sqlite")
    runtime = HeartbeatRuntime(store, sink=lambda message: (_ for _ in ()).throw(RuntimeError("boom")))

    try:
        runtime.tick()
    except HeartbeatSinkError as exc:
        assert isinstance(exc.__cause__, RuntimeError)
    else:
        raise AssertionError("expected HeartbeatSinkError")

    assert store.recent_heartbeats()[0].delivery_state == "UNCONFIRMED"


def test_crash_after_claim_remains_pending_and_preserves_due_time(tmp_path):
    path = tmp_path / "crash-after-claim.sqlite"
    first = TelemetryStore(path)
    claimed = first.claim_heartbeat(1_000)
    assert claimed is not None
    assert claimed["heartbeat_sequence"] == 1
    first.close()

    reopened = TelemetryStore(path)
    record = reopened.recent_heartbeats()[0]
    assert record.delivery_state == "PENDING"
    assert record.missed_intervals == 0
    assert reopened.milliseconds_until_heartbeat(11_000) == 50_000

    second = reopened.claim_heartbeat(1_000 + HEARTBEAT_INTERVAL_MS)
    assert second is not None
    assert second["heartbeat_sequence"] == 2
    assert second["missed_intervals"] == 0
    records = reopened.recent_heartbeats()
    assert [item.delivery_state for item in records] == ["PENDING", "PENDING"]
