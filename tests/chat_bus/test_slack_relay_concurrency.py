from __future__ import annotations

import threading

from chat_bus.slack_relay import RelayEnvelope, RelayError, RelayStore


def envelope() -> RelayEnvelope:
    return RelayEnvelope(
        operation_id="relay-race-op-001",
        message_id="relay-race-msg-001",
        sender_endpoint="one",
        recipient_endpoint="delta",
        channel_id="C0BNMJ337V4",
        body="Race the durable relay state.",
        created_at_ms=1_786_135_643_000,
    )


class PausePrewriteSelect:
    """Force legacy SELECT-then-write implementations to observe the same state.

    Implementations that perform an atomic write first never pause here, so the
    regression remains compatible with the intended fix rather than depending on
    a particular future query sequence.
    """

    def __init__(self, connection, barrier: threading.Barrier) -> None:
        self._connection = connection
        self._barrier = barrier
        self._write_seen = False

    def execute(self, sql, parameters=()):
        normalized = " ".join(sql.strip().lower().split())
        if normalized.startswith("insert ") or normalized.startswith("update "):
            self._write_seen = True
        cursor = self._connection.execute(sql, parameters)
        if (
            not self._write_seen
            and normalized.startswith("select * from relay_operations where operation_id")
        ):
            try:
                self._barrier.wait(2)
            except threading.BrokenBarrierError:
                pass
        return cursor

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._connection.__exit__(exc_type, exc, tb)

    def __getattr__(self, name):
        return getattr(self._connection, name)


def test_shared_store_can_claim_same_operation_from_two_threads(tmp_path):
    store = RelayStore(tmp_path / "shared.sqlite")
    item = envelope()
    start = threading.Barrier(2)
    results = []
    errors = []

    def worker():
        try:
            start.wait(2)
            results.append(store.claim(item))
        except Exception as exc:  # captured for assertion in the parent thread
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert sorted(result.created for result in results) == [False, True]
        assert len({result.envelope_sha256 for result in results}) == 1
    finally:
        store.close()


def test_equivalent_claim_race_has_one_created_winner_and_one_idempotent_replay(tmp_path):
    path = tmp_path / "claim-race.sqlite"
    bootstrap = RelayStore(path)
    bootstrap.close()
    item = envelope()
    barrier = threading.Barrier(2)
    results = []
    errors = []

    def worker():
        store = RelayStore(path)
        store.connection = PausePrewriteSelect(store.connection, barrier)
        try:
            results.append(store.claim(item))
        except Exception as exc:
            errors.append(exc)
        finally:
            store.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert sorted(result.created for result in results) == [False, True]

    verify = RelayStore(path)
    try:
        durable = verify.claim(item)
        assert durable.created is False
        for result in results:
            assert result.operation_id == durable.operation_id
            assert result.message_id == durable.message_id
            assert result.envelope_sha256 == durable.envelope_sha256
            assert result.slack_message_ts == durable.slack_message_ts
    finally:
        verify.close()


def test_delivery_race_returns_only_durable_winner_and_conflicts_loser(tmp_path):
    path = tmp_path / "delivery-race.sqlite"
    item = envelope()
    bootstrap = RelayStore(path)
    bootstrap.claim(item)
    bootstrap.close()

    barrier = threading.Barrier(2)
    timestamps = ("1786136000.000001", "1786136000.000002")
    results = []
    errors = []

    def worker(slack_ts: str):
        store = RelayStore(path)
        store.connection = PausePrewriteSelect(store.connection, barrier)
        try:
            results.append(store.mark_delivered(item.operation_id, item.sha256, slack_ts))
        except Exception as exc:
            errors.append(exc)
        finally:
            store.close()

    threads = [threading.Thread(target=worker, args=(ts,)) for ts in timestamps]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], RelayError)
    assert str(errors[0]) == "DELIVERY_RECEIPT_CONFLICT"

    verify = RelayStore(path)
    try:
        durable = verify.claim(item)
        assert durable.created is False
        assert durable.slack_message_ts in timestamps
        assert results[0].slack_message_ts == durable.slack_message_ts
    finally:
        verify.close()
