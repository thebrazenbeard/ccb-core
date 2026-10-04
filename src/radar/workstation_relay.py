"""Fail-closed workstation-mediated inter-chat relay primitives.

The relay is a visible-text fallback transport. Canonical authority remains the
Chat Communication Bus; this module does not grant direct chat API access,
hidden-state access, or protected-effect authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re


PROTOCOL = "BT2_WORKSTATION_RELAY_V1"
ACK_PROTOCOL = "BT2_WORKSTATION_RELAY_ACK_V1"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_NONCE_RE = re.compile(r"^[A-Fa-f0-9]{32,128}$")
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")


class RelayError(ValueError):
    """Raised when relay input cannot be admitted safely."""


def canonical_body(body: str) -> str:
    if not isinstance(body, str):
        raise RelayError("BODY_MUST_BE_TEXT")
    normalized = body.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        raise RelayError("BODY_REQUIRED")
    return normalized


@dataclass(frozen=True)
class RelayEnvelope:
    sender: str
    recipient: str
    message_id: str
    nonce: str
    source_bus_message_id: str
    source_bus_commit: str
    body: str
    body_sha256: str
    requires_ack: bool = True
    content_class: str = "CANARY"
    protocol: str = PROTOCOL

    @classmethod
    def create(
        cls,
        *,
        sender: str,
        recipient: str,
        message_id: str,
        nonce: str,
        source_bus_message_id: str,
        source_bus_commit: str,
        body: str,
    ) -> "RelayEnvelope":
        for label, value in (
            ("sender", sender),
            ("recipient", recipient),
            ("message_id", message_id),
            ("source_bus_message_id", source_bus_message_id),
        ):
            if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
                raise RelayError(f"INVALID_{label.upper()}")
        if not isinstance(nonce, str) or _NONCE_RE.fullmatch(nonce) is None:
            raise RelayError("INVALID_NONCE")
        if (
            not isinstance(source_bus_commit, str)
            or _SHA40_RE.fullmatch(source_bus_commit) is None
        ):
            raise RelayError("INVALID_SOURCE_BUS_COMMIT")

        normalized_body = canonical_body(body)
        digest = hashlib.sha256(normalized_body.encode("utf-8")).hexdigest()
        return cls(
            sender=sender,
            recipient=recipient,
            message_id=message_id,
            nonce=nonce.lower(),
            source_bus_message_id=source_bus_message_id,
            source_bus_commit=source_bus_commit,
            body=normalized_body,
            body_sha256=digest,
        )

    def render(self) -> str:
        return "\n".join(
            (
                self.protocol,
                f"sender: {self.sender}",
                f"recipient: {self.recipient}",
                f"message_id: {self.message_id}",
                f"nonce: {self.nonce}",
                f"source_bus_message_id: {self.source_bus_message_id}",
                f"source_bus_commit: {self.source_bus_commit}",
                f"body_sha256: {self.body_sha256}",
                "requires_ack: true",
                f"content_class: {self.content_class}",
                "---",
                self.body,
            )
        )


__all__ = [
    "ACK_PROTOCOL",
    "PROTOCOL",
    "RelayEnvelope",
    "RelayError",
    "canonical_body",
]
