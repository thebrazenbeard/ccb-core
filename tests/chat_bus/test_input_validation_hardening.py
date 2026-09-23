from __future__ import annotations

import pytest

from chat_bus import Ledger, LedgerError
from chat_bus.identity import IdentityError, LogicalEndpoint
from chat_bus.slack_relay import RelayEnvelope, RelayError


DIGEST = "c" * 64


def _confirmed_ledger() -> Ledger:
    ledger = Ledger()
    ledger.create_assignment("A", "WF", created_at_ms=1)
    transitions = (
        ("ASSIGN", "COORDINATOR"),
        ("ACKNOWLEDGE", "PRODUCER"),
        ("START", "PRODUCER"),
        ("REQUEST_REVIEW", "PRODUCER"),
        ("CONFIRM", "REVIEWER"),
    )
    for index, (transition, actor) in enumerate(transitions, start=1):
        ledger.apply_transition("A", transition, actor, f"op-{index}", DIGEST, created_at_ms=index + 1)
    return ledger


def test_final_acceptance_requires_actual_boolean_readback():
    ledger = _confirmed_ledger()
    with pytest.raises(LedgerError, match="READBACK_CONFIRMED_MUST_BE_BOOL"):
        ledger.record_final_acceptance(
            "A",
            "NATIVE_MULTI_AGENT",
            "PASS",
            "1",  # type: ignore[arg-type]
            DIGEST,
            created_at_ms=10,
        )


def test_relay_envelope_rejects_boolean_timestamp():
    with pytest.raises(RelayError, match="INVALID_CREATED_AT_MS"):
        RelayEnvelope(
            operation_id="op-1",
            message_id="msg-1",
            sender_endpoint="one",
            recipient_endpoint="two",
            channel_id="C123",
            body="hello",
            created_at_ms=True,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("field", "kwargs", "error"),
    [
        ("display_name", {"display_name": 7, "project_scope": "project"}, "DISPLAY_NAME_REQUIRED"),
        ("project_scope", {"display_name": "One", "project_scope": 7}, "PROJECT_SCOPE_REQUIRED"),
        (
            "slack_user_id",
            {"display_name": "One", "project_scope": "project", "slack_user_id": 7},
            "INVALID_SLACK_USER_ID",
        ),
    ],
)
def test_logical_endpoint_rejects_non_text_fields_deterministically(field, kwargs, error):
    del field
    with pytest.raises(IdentityError, match=error):
        LogicalEndpoint.create("one", **kwargs)  # type: ignore[arg-type]
