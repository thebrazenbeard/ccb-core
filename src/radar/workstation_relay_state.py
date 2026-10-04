"""Durable relay attempt state and evidence receipts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import sqlite3
from pathlib import Path


class RelayStateError(ValueError):
    pass


@dataclass(frozen=True)
class RelayReceipt:
    protocol: str
    sender: str
    recipient: str
    message_id: str
    nonce: str
    source_bus_message_id: str
    source_bus_commit: str
    body_sha256: str
    final_state: str
    transitions: tuple[str, ...]
    transition_times: dict[str, str]
    target_window_handle: int | None
    prewrite_selector_digest: str | None
    presubmit_selector_digest: str | None
    rendered_message_verified: bool
    ack_status: str | None
    ack_sha256: str | None
    side_effect_beyond_visible_text: bool = False

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["transitions"] = list(self.transitions)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "RelayReceipt":
        transitions = value.get("transitions")
        times = value.get("transition_times")
        if not isinstance(transitions, list) or not isinstance(times, dict):
            raise RelayStateError("INVALID_RELAY_RECEIPT")
        return cls(
            protocol=str(value["protocol"]),
            sender=str(value["sender"]),
            recipient=str(value["recipient"]),
            message_id=str(value["message_id"]),
            nonce=str(value["nonce"]),
            source_bus_message_id=str(value["source_bus_message_id"]),
            source_bus_commit=str(value["source_bus_commit"]),
            body_sha256=str(value["body_sha256"]),
            final_state=str(value["final_state"]),
            transitions=tuple(str(item) for item in transitions),
            transition_times={str(k): str(v) for k, v in times.items()},
            target_window_handle=(
                int(value["target_window_handle"])
                if value.get("target_window_handle") is not None
                else None
            ),
            prewrite_selector_digest=(
                str(value["prewrite_selector_digest"])
                if value.get("prewrite_selector_digest") is not None
                else None
            ),
            presubmit_selector_digest=(
                str(value["presubmit_selector_digest"])
                if value.get("presubmit_selector_digest") is not None
                else None
            ),
            rendered_message_verified=bool(value["rendered_message_verified"]),
            ack_status=(
                str(value["ack_status"]) if value.get("ack_status") is not None else None
            ),
            ack_sha256=(
                str(value["ack_sha256"]) if value.get("ack_sha256") is not None else None
            ),
            side_effect_beyond_visible_text=bool(
                value.get("side_effect_beyond_visible_text", False)
            ),
        )


class RelayStore:
    """SQLite-backed idempotency and state store for relay attempts."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(path))
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workstation_relay_operations (
                message_id TEXT PRIMARY KEY,
                nonce TEXT NOT NULL,
                envelope_sha256 TEXT NOT NULL,
                state TEXT NOT NULL,
                receipt_json TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def claim(
        self,
        *,
        message_id: str,
        nonce: str,
        envelope_sha256: str,
        updated_at: str,
    ) -> tuple[bool, str, RelayReceipt | None]:
        row = self.connection.execute(
            """
            SELECT nonce, envelope_sha256, state, receipt_json
            FROM workstation_relay_operations
            WHERE message_id = ?
            """,
            (message_id,),
        ).fetchone()
        if row is not None:
            if row[0] != nonce or row[1] != envelope_sha256:
                raise RelayStateError("MESSAGE_ID_CONFLICT")
            receipt = (
                RelayReceipt.from_dict(json.loads(row[3]))
                if row[3] is not None
                else None
            )
            return False, str(row[2]), receipt

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO workstation_relay_operations (
                    message_id, nonce, envelope_sha256, state, receipt_json, updated_at
                ) VALUES (?, ?, ?, 'CREATED', NULL, ?)
                """,
                (message_id, nonce, envelope_sha256, updated_at),
            )
        return True, "CREATED", None

    def set_state(
        self,
        *,
        message_id: str,
        nonce: str,
        envelope_sha256: str,
        state: str,
        updated_at: str,
    ) -> None:
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE workstation_relay_operations
                SET state = ?, updated_at = ?
                WHERE message_id = ? AND nonce = ? AND envelope_sha256 = ?
                """,
                (state, updated_at, message_id, nonce, envelope_sha256),
            )
        if cursor.rowcount != 1:
            raise RelayStateError("RELAY_STATE_BINDING_LOST")

    def finalize(
        self,
        *,
        message_id: str,
        nonce: str,
        envelope_sha256: str,
        receipt: RelayReceipt,
        updated_at: str,
    ) -> None:
        payload = json.dumps(
            receipt.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE workstation_relay_operations
                SET state = ?, receipt_json = ?, updated_at = ?
                WHERE message_id = ? AND nonce = ? AND envelope_sha256 = ?
                """,
                (
                    receipt.final_state,
                    payload,
                    updated_at,
                    message_id,
                    nonce,
                    envelope_sha256,
                ),
            )
        if cursor.rowcount != 1:
            raise RelayStateError("RELAY_RECEIPT_BINDING_LOST")


__all__ = ["RelayReceipt", "RelayStateError", "RelayStore"]
