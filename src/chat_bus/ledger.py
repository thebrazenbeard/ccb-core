"""SQLite-backed lifecycle ledger with database-enforced invariants."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from .digests import require_sha256


class LedgerError(RuntimeError):
    """Raised when a ledger operation violates the durable contract."""


@dataclass(frozen=True)
class AssignmentSnapshot:
    assignment_id: str
    workflow_id: str
    architecture_mode: str
    current_state: str
    current_transition_event_id: int | None
    pre_pause_state: str | None
    pre_interrupt_state: str | None
    nested_pause_state: str | None
    superseded_by_assignment_id: str | None


class Ledger:
    """A small transactional facade over the authoritative SQLite schema."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA recursive_triggers = ON")
        self.connection.executescript(files("chat_bus").joinpath("schema.sql").read_text())

    def close(self) -> None:
        self.connection.close()

    def create_assignment(
        self,
        assignment_id: str,
        workflow_id: str,
        architecture_mode: str = "NATIVE_MULTI_AGENT",
        *,
        created_at_ms: int | None = None,
    ) -> None:
        created = int(time.time() * 1000) if created_at_ms is None else created_at_ms
        try:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO assignments (
                        assignment_id, workflow_id, architecture_mode, created_at_ms
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (assignment_id, workflow_id, architecture_mode, created),
                )
        except sqlite3.IntegrityError as exc:
            raise LedgerError(str(exc)) from exc

    def apply_transition(
        self,
        assignment_id: str,
        transition_type_id: str,
        actor: str,
        operation_id: str,
        receipt_sha256: str,
        *,
        target_assignment_id: str | None = None,
        created_at_ms: int | None = None,
    ) -> int:
        digest = require_sha256(receipt_sha256, field="receipt_sha256")
        created = int(time.time() * 1000) if created_at_ms is None else created_at_ms
        snapshot = self.inspect(assignment_id)
        rule = self.connection.execute(
            """
            SELECT from_state, to_state, actor
            FROM transition_rules
            WHERE transition_type_id = ?
            """,
            (transition_type_id,),
        ).fetchone()
        if rule is None:
            raise LedgerError("UNKNOWN_TRANSITION_TYPE")
        if rule["actor"] != actor:
            raise LedgerError("ACTOR_MISMATCH")

        try:
            with self.connection:
                cursor = self.connection.execute(
                    """
                    INSERT INTO transition_events (
                        assignment_id, transition_type_id, actor, from_state, to_state,
                        predecessor_event_id, operation_id, receipt_sha256,
                        target_assignment_id, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        assignment_id,
                        transition_type_id,
                        actor,
                        snapshot.current_state,
                        rule["to_state"],
                        snapshot.current_transition_event_id,
                        operation_id,
                        digest,
                        target_assignment_id,
                        created,
                    ),
                )
                event_id = int(cursor.lastrowid)
                projected = self.connection.execute(
                    """
                    SELECT current_transition_event_id, current_state
                    FROM assignments WHERE assignment_id = ?
                    """,
                    (assignment_id,),
                ).fetchone()
                if projected is None or projected["current_transition_event_id"] != event_id:
                    raise LedgerError("PROJECTION_NOT_ADVANCED")
                if projected["current_state"] != rule["to_state"]:
                    raise LedgerError("PROJECTION_STATE_MISMATCH")
                return event_id
        except (sqlite3.IntegrityError, sqlite3.OperationalError) as exc:
            raise LedgerError(str(exc)) from exc

    def inspect(self, assignment_id: str) -> AssignmentSnapshot:
        row = self.connection.execute(
            "SELECT * FROM assignments WHERE assignment_id = ?",
            (assignment_id,),
        ).fetchone()
        if row is None:
            raise LedgerError("NOT_FOUND")
        return AssignmentSnapshot(
            assignment_id=row["assignment_id"],
            workflow_id=row["workflow_id"],
            architecture_mode=row["architecture_mode"],
            current_state=row["current_state"],
            current_transition_event_id=row["current_transition_event_id"],
            pre_pause_state=row["pre_pause_state"],
            pre_interrupt_state=row["pre_interrupt_state"],
            nested_pause_state=row["nested_pause_state"],
            superseded_by_assignment_id=row["superseded_by_assignment_id"],
        )

    def record_final_acceptance(
        self,
        assignment_id: str,
        architecture_mode: str,
        disposition: str,
        readback_confirmed: bool,
        evidence_sha256: str,
        *,
        created_at_ms: int | None = None,
    ) -> int:
        if type(readback_confirmed) is not bool:
            raise LedgerError("READBACK_CONFIRMED_MUST_BE_BOOL")
        digest = require_sha256(evidence_sha256, field="evidence_sha256")
        created = int(time.time() * 1000) if created_at_ms is None else created_at_ms
        try:
            with self.connection:
                cursor = self.connection.execute(
                    """
                    INSERT INTO final_acceptance_receipts (
                        assignment_id, architecture_mode, disposition,
                        readback_confirmed, evidence_sha256, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        assignment_id,
                        architecture_mode,
                        disposition,
                        int(readback_confirmed),
                        digest,
                        created,
                    ),
                )
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise LedgerError(str(exc)) from exc

    def execute_direct(self, sql: str, parameters: tuple[Any, ...] = ()) -> None:
        """Execute direct SQL for hostile tests; production callers should not use this."""

        try:
            with self.connection:
                self.connection.execute(sql, parameters)
        except (sqlite3.IntegrityError, sqlite3.OperationalError) as exc:
            raise LedgerError(str(exc)) from exc
