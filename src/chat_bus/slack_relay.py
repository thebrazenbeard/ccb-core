"""Slack-facing logical identity relay primitives for Chat Bus.

This module deliberately does not own Slack credentials or network I/O. It produces
validated outbound payloads and durable local delivery claims so a transport adapter
can call Slack without conflating transport identity with Chat Bus endpoint identity.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path

from .identity import EndpointRegistry, IdentityError

_SCHEMA = "CHAT_BUS_SLACK_RELAY_V1"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CHANNEL_RE = re.compile(r"^[CDG][A-Z0-9]+$")
_TS_RE = re.compile(r"^[0-9]+\.[0-9]+$")
_BOT_MENTION_RE = re.compile(r"^<@(?P<bot>[UW][A-Z0-9]+)>\s*")
_AT_ADDRESS_RE = re.compile(r"^@(?P<target>[A-Za-z][A-Za-z0-9_-]{0,63})\s+(?P<body>.+)$", re.DOTALL)
_COLON_ADDRESS_RE = re.compile(r"^(?P<target>[A-Za-z][A-Za-z0-9_-]{0,63})\s*:\s*(?P<body>.+)$", re.DOTALL)


class RelayError(RuntimeError):
    """Raised when a relay operation cannot be performed safely."""


@dataclass(frozen=True)
class RelayEnvelope:
    """Deterministic transport-neutral envelope for a Slack relay operation."""

    operation_id: str
    message_id: str
    sender_endpoint: str
    recipient_endpoint: str
    channel_id: str
    body: str
    created_at_ms: int
    thread_ts: str | None = None
    reply_to_message_id: str | None = None
    schema: str = _SCHEMA

    def __post_init__(self) -> None:
        for field_name, value in (
            ("operation_id", self.operation_id),
            ("message_id", self.message_id),
        ):
            if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
                raise RelayError(f"INVALID_{field_name.upper()}")
        if self.schema != _SCHEMA:
            raise RelayError("UNKNOWN_RELAY_SCHEMA")
        if _CHANNEL_RE.fullmatch(self.channel_id) is None:
            raise RelayError("INVALID_SLACK_CHANNEL_ID")
        if not isinstance(self.body, str) or not self.body.strip():
            raise RelayError("MESSAGE_BODY_REQUIRED")
        if len(self.body) > 40_000:
            raise RelayError("MESSAGE_BODY_TOO_LARGE")
        if type(self.created_at_ms) is not int or self.created_at_ms < 0:
            raise RelayError("INVALID_CREATED_AT_MS")
        if self.thread_ts is not None and _TS_RE.fullmatch(self.thread_ts) is None:
            raise RelayError("INVALID_THREAD_TS")
        if self.reply_to_message_id is not None and _ID_RE.fullmatch(self.reply_to_message_id) is None:
            raise RelayError("INVALID_REPLY_TO_MESSAGE_ID")

    def canonical_bytes(self) -> bytes:
        payload = asdict(self)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


@dataclass(frozen=True)
class AddressedText:
    recipient_endpoint: str
    body: str


@dataclass(frozen=True)
class SlackOutboundMessage:
    channel: str
    text: str
    thread_ts: str | None
    username: str | None
    icon_emoji: str | None
    metadata: dict[str, object]

    def as_chat_post_message(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "channel": self.channel,
            "text": self.text,
            "metadata": self.metadata,
        }
        if self.thread_ts is not None:
            payload["thread_ts"] = self.thread_ts
        if self.username is not None:
            payload["username"] = self.username
        if self.icon_emoji is not None:
            payload["icon_emoji"] = self.icon_emoji
        return payload


def parse_addressed_text(
    text: str,
    registry: EndpointRegistry,
    *,
    relay_bot_user_id: str | None = None,
) -> AddressedText:
    """Resolve explicit logical addressing and reject ambiguous/unaddressed text.

    Accepted forms after an optional relay-bot mention are:
      @delta review this
      delta: review this
    """

    if not isinstance(text, str) or not text.strip():
        raise RelayError("EMPTY_INBOUND_TEXT")
    candidate = text.strip()

    bot_match = _BOT_MENTION_RE.match(candidate)
    if bot_match is not None:
        if relay_bot_user_id is None or bot_match.group("bot") != relay_bot_user_id:
            raise RelayError("UNEXPECTED_SLACK_BOT_MENTION")
        candidate = candidate[bot_match.end():].lstrip()

    match = _AT_ADDRESS_RE.match(candidate) or _COLON_ADDRESS_RE.match(candidate)
    if match is None:
        raise RelayError("MISSING_LOGICAL_RECIPIENT")

    try:
        endpoint = registry.resolve(match.group("target"))
    except IdentityError as exc:
        raise RelayError("UNKNOWN_LOGICAL_RECIPIENT") from exc

    body = match.group("body").strip()
    if not body:
        raise RelayError("MESSAGE_BODY_REQUIRED")
    return AddressedText(recipient_endpoint=endpoint.endpoint_id, body=body)


def render_outbound(
    envelope: RelayEnvelope,
    registry: EndpointRegistry,
    *,
    customize_identity: bool = True,
) -> SlackOutboundMessage:
    """Render an envelope to a Slack Web API payload without leaking message body in metadata."""

    try:
        sender = registry.resolve(envelope.sender_endpoint)
        recipient = registry.resolve(envelope.recipient_endpoint)
    except IdentityError as exc:
        raise RelayError("UNKNOWN_ENVELOPE_ENDPOINT") from exc

    signpost = f"[Chat Bus relay · {sender.display_name} → {recipient.display_name}]"
    text = f"{signpost}\n{envelope.body}"
    metadata = {
        "event_type": "chat_bus_relay",
        "event_payload": {
            "schema": envelope.schema,
            "operation_id": envelope.operation_id,
            "message_id": envelope.message_id,
            "sender_endpoint": sender.endpoint_id,
            "recipient_endpoint": recipient.endpoint_id,
            "envelope_sha256": envelope.sha256,
        },
    }

    return SlackOutboundMessage(
        channel=envelope.channel_id,
        text=text,
        thread_ts=envelope.thread_ts,
        username=sender.display_name if customize_identity else None,
        icon_emoji=sender.icon_emoji if customize_identity else None,
        metadata=metadata,
    )


@dataclass(frozen=True)
class RelayClaim:
    operation_id: str
    message_id: str
    envelope_sha256: str
    slack_message_ts: str | None
    created: bool


class RelayStore:
    """SQLite-backed idempotency and exact-delivery receipt store."""

    def __init__(self, path: str | Path = ":memory:", *, timeout: float = 5.0) -> None:
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(
            path,
            timeout=timeout,
            check_same_thread=False,
        )
        self.connection.row_factory = sqlite3.Row
        with self._lock:
            self.connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS relay_operations (
                    operation_id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL UNIQUE,
                    envelope_sha256 TEXT NOT NULL CHECK (
                        length(envelope_sha256) = 64
                        AND envelope_sha256 NOT GLOB '*[^0-9a-f]*'
                    ),
                    channel_id TEXT NOT NULL,
                    slack_message_ts TEXT,
                    created_at_ms INTEGER NOT NULL
                );
                """
            )

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    @staticmethod
    def _claim_from_row(row: sqlite3.Row, *, created: bool) -> RelayClaim:
        return RelayClaim(
            operation_id=row["operation_id"],
            message_id=row["message_id"],
            envelope_sha256=row["envelope_sha256"],
            slack_message_ts=row["slack_message_ts"],
            created=created,
        )

    def claim(self, envelope: RelayEnvelope) -> RelayClaim:
        digest = envelope.sha256
        with self._lock:
            try:
                with self.connection:
                    cursor = self.connection.execute(
                        """
                        INSERT INTO relay_operations (
                            operation_id, message_id, envelope_sha256, channel_id, created_at_ms
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(operation_id) DO NOTHING
                        """,
                        (
                            envelope.operation_id,
                            envelope.message_id,
                            digest,
                            envelope.channel_id,
                            envelope.created_at_ms,
                        ),
                    )
                    created = cursor.rowcount == 1
                    row = self.connection.execute(
                        "SELECT * FROM relay_operations WHERE operation_id = ?",
                        (envelope.operation_id,),
                    ).fetchone()
            except sqlite3.IntegrityError as exc:
                row = self.connection.execute(
                    "SELECT * FROM relay_operations WHERE operation_id = ?",
                    (envelope.operation_id,),
                ).fetchone()
                if row is not None and (
                    row["envelope_sha256"] != digest or row["message_id"] != envelope.message_id
                ):
                    raise RelayError("OPERATION_ID_CONFLICT") from exc
                raise RelayError("MESSAGE_ID_CONFLICT") from exc

            if row is None:
                raise RelayError("RELAY_CLAIM_STATE_MISSING")
            if row["envelope_sha256"] != digest or row["message_id"] != envelope.message_id:
                raise RelayError("OPERATION_ID_CONFLICT")
            return self._claim_from_row(row, created=created)

    def mark_delivered(self, operation_id: str, envelope_sha256: str, slack_message_ts: str) -> RelayClaim:
        if _TS_RE.fullmatch(slack_message_ts) is None:
            raise RelayError("INVALID_SLACK_MESSAGE_TS")

        with self._lock:
            with self.connection:
                self.connection.execute(
                    """
                    UPDATE relay_operations
                    SET slack_message_ts = ?
                    WHERE operation_id = ?
                      AND envelope_sha256 = ?
                      AND (slack_message_ts IS NULL OR slack_message_ts = ?)
                    """,
                    (
                        slack_message_ts,
                        operation_id,
                        envelope_sha256,
                        slack_message_ts,
                    ),
                )
                row = self.connection.execute(
                    "SELECT * FROM relay_operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()

            if row is None:
                raise RelayError("UNKNOWN_RELAY_OPERATION")
            if row["envelope_sha256"] != envelope_sha256:
                raise RelayError("ENVELOPE_DIGEST_MISMATCH")
            if row["slack_message_ts"] != slack_message_ts:
                raise RelayError("DELIVERY_RECEIPT_CONFLICT")
            return self._claim_from_row(row, created=False)
