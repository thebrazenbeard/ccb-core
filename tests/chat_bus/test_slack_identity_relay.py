from dataclasses import replace

import pytest

from chat_bus.identity import EndpointRegistry, IdentityError, LogicalEndpoint
from chat_bus.slack_relay import (
    RelayEnvelope,
    RelayError,
    RelayStore,
    parse_addressed_text,
    render_outbound,
)


def registry() -> EndpointRegistry:
    return EndpointRegistry(
        [
            LogicalEndpoint("one", "One", "build-team-two", aliases=("coordinator",), icon_emoji=":one:"),
            LogicalEndpoint("delta", "delta", "delta", aliases=("hep",), icon_emoji=":hammer_and_pick:"),
            LogicalEndpoint("zeta", "zeta", "masamune", icon_emoji=":crossed_swords:"),
            LogicalEndpoint("gamma", "gamma", "masamune", icon_emoji=":crossed_swords:"),
        ]
    )


def envelope() -> RelayEnvelope:
    return RelayEnvelope(
        operation_id="relay-op-001",
        message_id="msg-001",
        sender_endpoint="one",
        recipient_endpoint="delta",
        channel_id="C0BNMJ337V4",
        body="Review the Project routing boundary.",
        created_at_ms=1_786_135_643_000,
        thread_ts="1786135643.897769",
    )


def test_aliases_are_case_insensitive_and_collision_safe() -> None:
    endpoints = registry()
    assert endpoints.resolve("@Coordinator").endpoint_id == "one"
    assert endpoints.resolve("HEP").endpoint_id == "delta"

    with pytest.raises(IdentityError, match="LOGICAL_ADDRESS_COLLISION"):
        EndpointRegistry(
            [
                LogicalEndpoint("one", "One", "bt2", aliases=("shared",)),
                LogicalEndpoint("two", "Two", "bt2", aliases=("shared",)),
            ]
        )


def test_address_parser_requires_explicit_logical_recipient() -> None:
    endpoints = registry()
    parsed = parse_addressed_text("@delta inspect this", endpoints)
    assert parsed.recipient_endpoint == "delta"
    assert parsed.body == "inspect this"

    parsed = parse_addressed_text(
        "<@U0RELAYBOT> zeta: review the digest code",
        endpoints,
        relay_bot_user_id="U0RELAYBOT",
    )
    assert parsed.recipient_endpoint == "zeta"
    assert parsed.body == "review the digest code"

    with pytest.raises(RelayError, match="MISSING_LOGICAL_RECIPIENT"):
        parse_addressed_text("review the digest code", endpoints)
    with pytest.raises(RelayError, match="UNKNOWN_LOGICAL_RECIPIENT"):
        parse_addressed_text("@Unknown review this", endpoints)
    with pytest.raises(RelayError, match="UNEXPECTED_SLACK_BOT_MENTION"):
        parse_addressed_text("<@U0OTHERBOT> zeta: review this", endpoints, relay_bot_user_id="U0RELAYBOT")


def test_envelope_digest_is_deterministic_and_content_bound() -> None:
    first = envelope()
    second = envelope()
    assert first.sha256 == second.sha256
    assert len(first.sha256) == 64
    assert replace(first, body="different").sha256 != first.sha256


def test_outbound_is_signposted_and_metadata_excludes_body() -> None:
    outbound = render_outbound(envelope(), registry(), customize_identity=True)
    payload = outbound.as_chat_post_message()
    assert payload["username"] == "One"
    assert payload["icon_emoji"] == ":one:"
    assert payload["text"].startswith("[Chat Bus relay · One → delta]\n")
    event_payload = payload["metadata"]["event_payload"]
    assert event_payload["envelope_sha256"] == envelope().sha256
    assert "body" not in event_payload
    assert "Review the Project" not in repr(payload["metadata"])


def test_operation_replay_is_idempotent_and_divergent_reuse_fails() -> None:
    store = RelayStore()
    try:
        first = store.claim(envelope())
        assert first.created is True
        replay = store.claim(envelope())
        assert replay.created is False
        assert replay.envelope_sha256 == first.envelope_sha256

        with pytest.raises(RelayError, match="OPERATION_ID_CONFLICT"):
            store.claim(replace(envelope(), body="changed payload"))
    finally:
        store.close()


def test_delivery_receipt_is_exact_and_idempotent() -> None:
    store = RelayStore()
    try:
        item = envelope()
        store.claim(item)
        delivered = store.mark_delivered(item.operation_id, item.sha256, "1786136000.000001")
        assert delivered.slack_message_ts == "1786136000.000001"
        replay = store.mark_delivered(item.operation_id, item.sha256, "1786136000.000001")
        assert replay.slack_message_ts == delivered.slack_message_ts

        with pytest.raises(RelayError, match="DELIVERY_RECEIPT_CONFLICT"):
            store.mark_delivered(item.operation_id, item.sha256, "1786136000.000002")
        with pytest.raises(RelayError, match="ENVELOPE_DIGEST_MISMATCH"):
            store.mark_delivered(item.operation_id, "0" * 64, "1786136000.000001")
    finally:
        store.close()
