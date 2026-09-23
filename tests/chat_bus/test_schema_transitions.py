from __future__ import annotations

import sqlite3

import pytest

from chat_bus import Ledger, LedgerError


D = "c" * 64


def transition(ledger: Ledger, name: str, actor: str, seq: int) -> int:
    return ledger.apply_transition("A", name, actor, f"op-{seq}-{name}", D)


def advance_to(ledger: Ledger, state: str) -> None:
    if state == "DRAFTED":
        return
    transition(ledger, "ASSIGN", "COORDINATOR", 1)
    if state == "ASSIGNED":
        return
    transition(ledger, "ACKNOWLEDGE", "PRODUCER", 2)
    if state == "ACKNOWLEDGED":
        return
    transition(ledger, "START", "PRODUCER", 3)
    if state == "RUNNING":
        return
    transition(ledger, "REQUEST_REVIEW", "PRODUCER", 4)
    if state == "REVIEW_PENDING":
        return
    raise AssertionError(f"unsupported setup state {state}")


@pytest.mark.parametrize(
    ("source", "pause_transition", "resume_transition"),
    [
        ("ACKNOWLEDGED", "PAUSE_FROM_ACKNOWLEDGED", "RESUME_TO_ACKNOWLEDGED"),
        ("RUNNING", "PAUSE_FROM_RUNNING", "RESUME_TO_RUNNING"),
        ("REVIEW_PENDING", "PAUSE_FROM_REVIEW_PENDING", "RESUME_TO_REVIEW_PENDING"),
    ],
)
def test_pause_and_resume_preserve_exact_predecessor(
    source: str, pause_transition: str, resume_transition: str
) -> None:
    ledger = Ledger()
    ledger.create_assignment("A", "WF", created_at_ms=1)
    advance_to(ledger, source)
    transition(ledger, pause_transition, "OPERATOR", 10)
    paused = ledger.inspect("A")
    assert paused.current_state == "PAUSED_USAGE_EXHAUSTED"
    assert paused.pre_pause_state == source
    transition(ledger, resume_transition, "OPERATOR", 11)
    resumed = ledger.inspect("A")
    assert resumed.current_state == source
    assert resumed.pre_pause_state is None


def test_interrupt_from_pause_archives_nested_pause_and_recovers() -> None:
    ledger = Ledger()
    ledger.create_assignment("A", "WF", created_at_ms=1)
    advance_to(ledger, "REVIEW_PENDING")
    transition(ledger, "PAUSE_FROM_REVIEW_PENDING", "OPERATOR", 10)
    transition(ledger, "INTERRUPT_FROM_PAUSED", "OPERATOR", 11)
    interrupted = ledger.inspect("A")
    assert interrupted.current_state == "INTERRUPTED"
    assert interrupted.pre_pause_state is None
    assert interrupted.pre_interrupt_state == "PAUSED_USAGE_EXHAUSTED"
    assert interrupted.nested_pause_state == "REVIEW_PENDING"

    transition(ledger, "RECOVER_TO_PAUSED", "OPERATOR", 12)
    paused = ledger.inspect("A")
    assert paused.current_state == "PAUSED_USAGE_EXHAUSTED"
    assert paused.pre_pause_state == "REVIEW_PENDING"
    assert paused.pre_interrupt_state is None
    assert paused.nested_pause_state is None

    transition(ledger, "RESUME_TO_REVIEW_PENDING", "OPERATOR", 13)
    assert ledger.inspect("A").current_state == "REVIEW_PENDING"


@pytest.mark.parametrize(
    ("source", "setup", "stop_transition"),
    [
        ("DRAFTED", [], "STOP_FROM_DRAFTED"),
        ("ASSIGNED", [("ASSIGN", "COORDINATOR")], "STOP_FROM_ASSIGNED"),
        (
            "ACKNOWLEDGED",
            [("ASSIGN", "COORDINATOR"), ("ACKNOWLEDGE", "PRODUCER")],
            "STOP_FROM_ACKNOWLEDGED",
        ),
        (
            "RUNNING",
            [("ASSIGN", "COORDINATOR"), ("ACKNOWLEDGE", "PRODUCER"), ("START", "PRODUCER")],
            "STOP_FROM_RUNNING",
        ),
        (
            "REVIEW_PENDING",
            [
                ("ASSIGN", "COORDINATOR"),
                ("ACKNOWLEDGE", "PRODUCER"),
                ("START", "PRODUCER"),
                ("REQUEST_REVIEW", "PRODUCER"),
            ],
            "STOP_FROM_REVIEW_PENDING",
        ),
    ],
)
def test_stop_executes_from_declared_plain_sources(
    source: str, setup: list[tuple[str, str]], stop_transition: str
) -> None:
    ledger = Ledger()
    ledger.create_assignment("A", "WF", created_at_ms=1)
    for index, (name, actor) in enumerate(setup, start=1):
        transition(ledger, name, actor, index)
    assert ledger.inspect("A").current_state == source
    transition(ledger, stop_transition, "OPERATOR", 90)
    stopped = ledger.inspect("A")
    assert stopped.current_state == "STOPPED"
    assert stopped.pre_pause_state is None
    assert stopped.pre_interrupt_state is None
    assert stopped.nested_pause_state is None


def test_stop_from_paused_is_executable_and_clears_context() -> None:
    ledger = Ledger()
    ledger.create_assignment("A", "WF", created_at_ms=1)
    advance_to(ledger, "RUNNING")
    transition(ledger, "PAUSE_FROM_RUNNING", "OPERATOR", 10)
    transition(ledger, "STOP_FROM_PAUSED", "OPERATOR", 11)
    snapshot = ledger.inspect("A")
    assert snapshot.current_state == "STOPPED"
    assert snapshot.pre_pause_state is None


def test_stop_from_interrupted_is_executable_and_clears_context() -> None:
    ledger = Ledger()
    ledger.create_assignment("A", "WF", created_at_ms=1)
    advance_to(ledger, "RUNNING")
    transition(ledger, "INTERRUPT_FROM_RUNNING", "OPERATOR", 10)
    transition(ledger, "STOP_FROM_INTERRUPTED", "OPERATOR", 11)
    snapshot = ledger.inspect("A")
    assert snapshot.current_state == "STOPPED"
    assert snapshot.pre_interrupt_state is None
    assert snapshot.nested_pause_state is None


def test_interrupt_from_paused_then_stop_is_executable() -> None:
    ledger = Ledger()
    ledger.create_assignment("A", "WF", created_at_ms=1)
    advance_to(ledger, "RUNNING")
    transition(ledger, "PAUSE_FROM_RUNNING", "OPERATOR", 10)
    transition(ledger, "INTERRUPT_FROM_PAUSED", "OPERATOR", 11)
    transition(ledger, "STOP_FROM_INTERRUPTED", "OPERATOR", 12)
    snapshot = ledger.inspect("A")
    assert snapshot.current_state == "STOPPED"
    assert snapshot.pre_pause_state is None
    assert snapshot.pre_interrupt_state is None
    assert snapshot.nested_pause_state is None


def test_cross_assignment_predecessor_is_rejected_with_zero_state_effect() -> None:
    ledger = Ledger()
    ledger.create_assignment("A", "WF", created_at_ms=1)
    ledger.create_assignment("B", "WF", created_at_ms=2)
    event_b = ledger.apply_transition("B", "ASSIGN", "COORDINATOR", "b-1", D)
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)):
        with ledger.connection:
            ledger.connection.execute(
                """
                INSERT INTO transition_events (
                    assignment_id, transition_type_id, actor, from_state, to_state,
                    predecessor_event_id, operation_id, receipt_sha256, created_at_ms
                ) VALUES ('A','ASSIGN','COORDINATOR','DRAFTED','ASSIGNED',?,'bad',?,3)
                """,
                (event_b, D),
            )
    assert ledger.inspect("A").current_state == "DRAFTED"


@pytest.mark.parametrize("bad", ["a" + "Z" * 63, "A" * 64, "g" * 64, "a" * 63, "a" * 65])
def test_direct_sql_rejects_malformed_receipt_sha256(bad: str) -> None:
    ledger = Ledger()
    ledger.create_assignment("A", "WF", created_at_ms=1)
    with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)):
        with ledger.connection:
            ledger.connection.execute(
                """
                INSERT INTO transition_events (
                    assignment_id, transition_type_id, actor, from_state, to_state,
                    predecessor_event_id, operation_id, receipt_sha256, created_at_ms
                ) VALUES ('A','ASSIGN','COORDINATOR','DRAFTED','ASSIGNED',NULL,'bad',?,2)
                """,
                (bad,),
            )
    assert ledger.inspect("A").current_state == "DRAFTED"


def test_wrong_actor_is_rejected_with_zero_state_effect() -> None:
    ledger = Ledger()
    ledger.create_assignment("A", "WF", created_at_ms=1)
    with pytest.raises(LedgerError, match="ACTOR_MISMATCH"):
        ledger.apply_transition("A", "ASSIGN", "PRODUCER", "bad", D)
    assert ledger.inspect("A").current_state == "DRAFTED"
