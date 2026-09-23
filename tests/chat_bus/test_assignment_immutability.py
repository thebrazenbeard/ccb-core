from __future__ import annotations

import pytest

from chat_bus import Ledger, LedgerError


D = "b" * 64


def make_ledger(mode: str = "NATIVE_MULTI_AGENT") -> Ledger:
    ledger = Ledger()
    ledger.create_assignment("A", "WF", mode, created_at_ms=1)
    return ledger


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("workflow_id", "OTHER"),
        ("architecture_mode", "FALLBACK_COORDINATOR_RELAY"),
        ("assignment_id", "B"),
        ("created_at_ms", 2),
    ],
)
def test_assignment_identity_is_insert_only(column: str, value: object) -> None:
    ledger = make_ledger()
    with pytest.raises(LedgerError, match="IMMUTABLE_ASSIGNMENT_IDENTITY"):
        ledger.execute_direct(f"UPDATE assignments SET {column} = ? WHERE assignment_id = 'A'", (value,))
    snapshot = ledger.inspect("A")
    assert snapshot.assignment_id == "A"
    assert snapshot.workflow_id == "WF"
    assert snapshot.architecture_mode == "NATIVE_MULTI_AGENT"


def test_projection_cannot_be_changed_without_bound_transition() -> None:
    ledger = make_ledger()
    with pytest.raises(LedgerError, match="PROJECTION_REQUIRES_BOUND_TRANSITION"):
        ledger.execute_direct("UPDATE assignments SET current_state = 'CONFIRMED' WHERE assignment_id = 'A'")
    assert ledger.inspect("A").current_state == "DRAFTED"


def test_supersession_requires_explicit_transition_and_real_target() -> None:
    ledger = make_ledger()
    ledger.create_assignment("B", "WF", created_at_ms=2)
    with pytest.raises(LedgerError, match="PROJECTION_REQUIRES_BOUND_TRANSITION"):
        ledger.execute_direct("UPDATE assignments SET superseded_by_assignment_id = 'B' WHERE assignment_id = 'A'")

    ledger.apply_transition("A", "SUPERSEDE", "COORDINATOR", "op-super", D, target_assignment_id="B")
    snapshot = ledger.inspect("A")
    assert snapshot.current_state == "SUPERSEDED"
    assert snapshot.superseded_by_assignment_id == "B"


def test_final_receipt_mode_must_match_authoritative_assignment_mode() -> None:
    ledger = make_ledger("FALLBACK_COORDINATOR_RELAY")
    ledger.apply_transition("A", "ASSIGN", "COORDINATOR", "1", D)
    ledger.apply_transition("A", "ACKNOWLEDGE", "PRODUCER", "2", D)
    ledger.apply_transition("A", "START", "PRODUCER", "3", D)
    ledger.apply_transition("A", "REQUEST_REVIEW", "PRODUCER", "4", D)
    ledger.apply_transition("A", "CONFIRM", "REVIEWER", "5", D)

    with pytest.raises(LedgerError, match="ACCEPTANCE_ASSIGNMENT_MODE_OR_STATE_MISMATCH"):
        ledger.record_final_acceptance("A", "NATIVE_MULTI_AGENT", "PASS", True, D)

    receipt_id = ledger.record_final_acceptance(
        "A", "FALLBACK_COORDINATOR_RELAY", "PASS", True, D
    )
    assert receipt_id == 1


def test_final_pass_requires_readback() -> None:
    ledger = make_ledger()
    ledger.apply_transition("A", "ASSIGN", "COORDINATOR", "1", D)
    ledger.apply_transition("A", "ACKNOWLEDGE", "PRODUCER", "2", D)
    ledger.apply_transition("A", "START", "PRODUCER", "3", D)
    ledger.apply_transition("A", "REQUEST_REVIEW", "PRODUCER", "4", D)
    ledger.apply_transition("A", "CONFIRM", "REVIEWER", "5", D)
    with pytest.raises(LedgerError, match="PASS_REQUIRES_READBACK"):
        ledger.record_final_acceptance("A", "NATIVE_MULTI_AGENT", "PASS", False, D)
