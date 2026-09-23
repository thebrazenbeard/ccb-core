import importlib
import sqlite3
import threading

import pytest


def _module():
    try:
        return importlib.import_module("radar.dlq")
    except Exception as exc:
        pytest.fail(f"Radar DLQ implementation missing: {exc}")


def test_dead_letter_survives_store_reopen(tmp_path):
    m = _module()
    path = tmp_path / "radar-dlq.sqlite"
    store = m.DeadLetterStore(path)
    item_id = store.record(
        message_id="m1",
        failure_stage="routing",
        failure_code="NO_ROUTE",
        envelope={"message_id": "m1"},
        diagnostic="no recipients",
    )
    store.close()
    reopened = m.DeadLetterStore(path)
    item = reopened.inspect(item_id)
    assert item.failure_code == "NO_ROUTE"
    assert item.replay_state == "PENDING"


def test_replay_requires_applicability_refresh(tmp_path):
    m = _module()
    store = m.DeadLetterStore(tmp_path / "dlq.sqlite")
    item_id = store.record(
        message_id="m2",
        failure_stage="delivery",
        failure_code="OFFLINE",
        envelope={"message_id": "m2"},
        diagnostic="target offline",
    )
    with pytest.raises(m.DeadLetterError, match="APPLICABILITY_NOT_REFRESHED"):
        store.mark_replay(item_id)
    store.set_applicability(item_id, "APPLICABLE")
    store.mark_replay(item_id)
    assert store.inspect(item_id).replay_state == "REPLAYED"


def test_completed_replay_cannot_be_counted_twice(tmp_path):
    m = _module()
    store = m.DeadLetterStore(tmp_path / "replay-once.sqlite")
    item_id = store.record(
        message_id="m-replay",
        failure_stage="delivery",
        failure_code="OFFLINE",
        envelope={"message_id": "m-replay"},
        diagnostic="target offline",
    )
    store.set_applicability(item_id, "APPLICABLE")
    store.mark_replay(item_id)

    with pytest.raises(m.DeadLetterError, match="REPLAY_ALREADY_COMPLETED"):
        store.mark_replay(item_id)

    item = store.inspect(item_id)
    assert item.replay_state == "REPLAYED"
    assert item.retry_count == 1


def test_pending_list_rejects_non_positive_limit(tmp_path):
    m = _module()
    store = m.DeadLetterStore(tmp_path / "limit.sqlite")
    with pytest.raises(m.DeadLetterError, match="INVALID_LIMIT"):
        store.list_pending_applicable(0)
    with pytest.raises(m.DeadLetterError, match="INVALID_LIMIT"):
        store.list_pending_applicable(-1)


def test_dead_letter_preserves_source_locator_and_reason_category(tmp_path):
    m = _module()
    path = tmp_path / "evidence.sqlite"
    store = m.DeadLetterStore(path)
    item_id = store.record(
        message_id="m3",
        failure_stage="admission",
        failure_code="UNKNOWN_DOMAIN",
        envelope={"message_id": "m3", "domain": "bogus"},
        diagnostic="domain not registered",
        reason_category="TAXONOMY",
        source_locator="git:bus/one-v2@abc123:messages/one-0003.md",
    )
    store.close()

    reopened = m.DeadLetterStore(path)
    item = reopened.inspect(item_id)
    assert item.reason_category == "TAXONOMY"
    assert item.source_locator == "git:bus/one-v2@abc123:messages/one-0003.md"


def test_existing_dlq_database_is_migrated_additively(tmp_path):
    m = _module()
    path = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE radar_dead_letters (
            item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT NOT NULL,
            failure_stage TEXT NOT NULL,
            failure_code TEXT NOT NULL,
            envelope_json TEXT NOT NULL,
            diagnostic TEXT NOT NULL,
            applicability TEXT NOT NULL DEFAULT 'UNKNOWN',
            replay_state TEXT NOT NULL DEFAULT 'PENDING',
            retry_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    connection.execute(
        """
        INSERT INTO radar_dead_letters
            (message_id, failure_stage, failure_code, envelope_json, diagnostic)
        VALUES ('legacy-1', 'routing', 'NO_ROUTE', '{"message_id":"legacy-1"}', 'legacy')
        """
    )
    connection.commit()
    connection.close()

    store = m.DeadLetterStore(path)
    item = store.inspect(1)
    assert item.reason_category == "UNCLASSIFIED"
    assert item.source_locator is None


def test_dead_letter_store_supports_explicit_thread_sharing(tmp_path):
    m = _module()
    store = m.DeadLetterStore(
        tmp_path / "thread-shared.sqlite",
        check_same_thread=False,
    )
    start = threading.Barrier(3)
    errors = []
    ids = []
    result_lock = threading.Lock()

    def write(message_id):
        try:
            start.wait()
            item_id = store.record(
                message_id=message_id,
                failure_stage="admission",
                failure_code="UNKNOWN_TAXONOMY",
                envelope={"message_id": message_id},
                diagnostic="unknown taxonomy",
                reason_category="TAXONOMY",
            )
            with result_lock:
                ids.append(item_id)
        except Exception as exc:
            with result_lock:
                errors.append(exc)

    threads = [
        threading.Thread(target=write, args=("thread-1",)),
        threading.Thread(target=write, args=("thread-2",)),
    ]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(5)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(ids) == 2
    assert sorted(store.inspect(item_id).message_id for item_id in ids) == ["thread-1", "thread-2"]
