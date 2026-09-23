"""Durable SQLite dead-letter queue for Radar."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import sqlite3
import threading


class DeadLetterError(RuntimeError):
    pass


class Applicability(str, Enum):
    UNKNOWN = "UNKNOWN"
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ReplayState(str, Enum):
    PENDING = "PENDING"
    REPLAYED = "REPLAYED"
    DISCARDED = "DISCARDED"


@dataclass(frozen=True)
class DeadLetterItem:
    item_id: int
    message_id: str
    failure_stage: str
    failure_code: str
    envelope: dict[str, object]
    diagnostic: str
    reason_category: str
    source_locator: str | None
    applicability: str
    replay_state: str
    retry_count: int


class DeadLetterStore:
    """SQLite-backed dead-letter queue with replay and applicability tracking.

    SQLite same-thread protection remains enabled by default. Callers that
    deliberately share a store across threads may opt out; this object's RLock
    then serializes every operation on the shared connection.
    """

    MAX_RETRIES = 5

    def __init__(
        self,
        path: str | Path,
        *,
        timeout: float = 5.0,
        check_same_thread: bool = True,
    ) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(
            self.path,
            timeout=timeout,
            check_same_thread=check_same_thread,
        )
        self.connection.row_factory = sqlite3.Row
        with self._lock:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS radar_dead_letters (
                    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL,
                    failure_stage TEXT NOT NULL,
                    failure_code TEXT NOT NULL,
                    envelope_json TEXT NOT NULL,
                    diagnostic TEXT NOT NULL,
                    reason_category TEXT NOT NULL DEFAULT 'UNCLASSIFIED',
                    source_locator TEXT,
                    applicability TEXT NOT NULL DEFAULT 'UNKNOWN'
                        CHECK (applicability IN ('UNKNOWN','APPLICABLE','NOT_APPLICABLE')),
                    replay_state TEXT NOT NULL DEFAULT 'PENDING'
                        CHECK (replay_state IN ('PENDING','REPLAYED','DISCARDED')),
                    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0)
                )
                """
            )
            self._migrate_evidence_columns()
            self.connection.commit()

    def __enter__(self) -> DeadLetterStore:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.close()
        return False

    def _migrate_evidence_columns(self) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(radar_dead_letters)")
        }
        if "reason_category" not in columns:
            self.connection.execute(
                "ALTER TABLE radar_dead_letters "
                "ADD COLUMN reason_category TEXT NOT NULL DEFAULT 'UNCLASSIFIED'"
            )
        if "source_locator" not in columns:
            self.connection.execute(
                "ALTER TABLE radar_dead_letters ADD COLUMN source_locator TEXT"
            )

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def _row_to_item(self, row: sqlite3.Row) -> DeadLetterItem:
        return DeadLetterItem(
            item_id=int(row["item_id"]),
            message_id=row["message_id"],
            failure_stage=row["failure_stage"],
            failure_code=row["failure_code"],
            envelope=json.loads(row["envelope_json"]),
            diagnostic=row["diagnostic"],
            reason_category=row["reason_category"],
            source_locator=row["source_locator"],
            applicability=row["applicability"],
            replay_state=row["replay_state"],
            retry_count=int(row["retry_count"]),
        )

    def record(
        self,
        *,
        message_id: str,
        failure_stage: str,
        failure_code: str,
        envelope: dict[str, object],
        diagnostic: str,
        reason_category: str = "UNCLASSIFIED",
        source_locator: str | None = None,
    ) -> int:
        for value, code in (
            (message_id, "MESSAGE_ID_REQUIRED"),
            (failure_stage, "FAILURE_STAGE_REQUIRED"),
            (failure_code, "FAILURE_CODE_REQUIRED"),
            (diagnostic, "DIAGNOSTIC_REQUIRED"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise DeadLetterError(code)
        if not isinstance(reason_category, str) or not reason_category.strip():
            raise DeadLetterError("REASON_CATEGORY_REQUIRED")
        if source_locator is not None and (
            not isinstance(source_locator, str) or not source_locator.strip()
        ):
            raise DeadLetterError("INVALID_SOURCE_LOCATOR")
        if not isinstance(envelope, dict):
            raise DeadLetterError("ENVELOPE_REQUIRED")
        try:
            envelope_json = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise DeadLetterError("ENVELOPE_NOT_JSON_SERIALIZABLE") from exc

        with self._lock:
            with self.connection:
                cursor = self.connection.execute(
                    """
                    INSERT INTO radar_dead_letters
                        (
                            message_id,
                            failure_stage,
                            failure_code,
                            envelope_json,
                            diagnostic,
                            reason_category,
                            source_locator
                        )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id.strip(),
                        failure_stage.strip(),
                        failure_code.strip(),
                        envelope_json,
                        diagnostic.strip(),
                        reason_category.strip().upper(),
                        None if source_locator is None else source_locator.strip(),
                    ),
                )
            return int(cursor.lastrowid)

    def inspect(self, item_id: int) -> DeadLetterItem:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM radar_dead_letters WHERE item_id = ?", (item_id,)
            ).fetchone()
            if row is None:
                raise DeadLetterError("NOT_FOUND")
            return self._row_to_item(row)

    def set_applicability(self, item_id: int, applicability: str) -> None:
        if applicability not in {Applicability.APPLICABLE.value, Applicability.NOT_APPLICABLE.value}:
            raise DeadLetterError("INVALID_APPLICABILITY")
        with self._lock:
            with self.connection:
                cursor = self.connection.execute(
                    "UPDATE radar_dead_letters SET applicability = ? WHERE item_id = ?",
                    (applicability, item_id),
                )
            if cursor.rowcount != 1:
                raise DeadLetterError("NOT_FOUND")

    def list_pending_applicable(self, limit: int = 100) -> list[DeadLetterItem]:
        """Retrieve items ready for replay in deterministic insertion order."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise DeadLetterError("INVALID_LIMIT")
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT * FROM radar_dead_letters
                WHERE applicability = ? AND replay_state = ?
                ORDER BY item_id ASC
                LIMIT ?
                """,
                (Applicability.APPLICABLE.value, ReplayState.PENDING.value, limit),
            ).fetchall()
            return [self._row_to_item(row) for row in rows]

    def mark_replay(self, item_id: int) -> None:
        """Atomically mark one pending applicable item as replayed exactly once."""
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                row = self.connection.execute(
                    "SELECT * FROM radar_dead_letters WHERE item_id = ?", (item_id,)
                ).fetchone()
                if row is None:
                    raise DeadLetterError("NOT_FOUND")
                item = self._row_to_item(row)
                if item.replay_state != ReplayState.PENDING.value:
                    raise DeadLetterError("REPLAY_ALREADY_COMPLETED")
                if item.applicability == Applicability.UNKNOWN.value:
                    raise DeadLetterError("APPLICABILITY_NOT_REFRESHED")
                if item.applicability != Applicability.APPLICABLE.value:
                    raise DeadLetterError("MESSAGE_NOT_APPLICABLE")
                if item.retry_count >= self.MAX_RETRIES:
                    raise DeadLetterError("MAX_RETRIES_EXCEEDED")

                cursor = self.connection.execute(
                    """
                    UPDATE radar_dead_letters
                    SET replay_state = ?, retry_count = retry_count + 1
                    WHERE item_id = ? AND replay_state = ? AND applicability = ?
                    """,
                    (
                        ReplayState.REPLAYED.value,
                        item_id,
                        ReplayState.PENDING.value,
                        Applicability.APPLICABLE.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise DeadLetterError("REPLAY_STATE_CHANGED")
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise


__all__ = ["DeadLetterError", "DeadLetterItem", "DeadLetterStore", "Applicability", "ReplayState"]
