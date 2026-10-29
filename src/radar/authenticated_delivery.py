"""Host-bound receiver acknowledgment, separate from workstation text matching.

This module deliberately has no provider client, model tool loop, network call,
or implicit credential source. The host supplies three independent authorities:
an exact canonical-message reader, an authenticated receiver proof verifier,
and an actor/subject/privacy authorizer. None may be model-selected.

Successful acknowledgment establishes a durable receiver-side event only. It
does not attest that a model consumed or responded to the message.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
from typing import Callable, Protocol


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA40 = re.compile(r"^[a-f0-9]{40}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_NONCE = re.compile(r"^[a-f0-9]{32,128}$")


class DeliveryGateError(ValueError):
    """A fail-closed authorization, binding, or persistence error."""


def _require_id(name: str, value: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise DeliveryGateError(f"INVALID_{name.upper()}")


@dataclass(frozen=True)
class DeliveryBinding:
    """Expected metadata obtained by the host from the canonical Bus source."""

    message_id: str
    source_commit: str
    content_sha256: str
    recipient: str
    subject: str
    privacy_scope: str
    nonce: str

    def __post_init__(self) -> None:
        for name in ("message_id", "recipient", "subject", "privacy_scope"):
            _require_id(name, getattr(self, name))
        if not isinstance(self.source_commit, str) or _SHA40.fullmatch(self.source_commit) is None:
            raise DeliveryGateError("INVALID_SOURCE_COMMIT")
        if not isinstance(self.content_sha256, str) or _SHA256.fullmatch(self.content_sha256) is None:
            raise DeliveryGateError("INVALID_CONTENT_SHA256")
        if not isinstance(self.nonce, str) or _NONCE.fullmatch(self.nonce) is None:
            raise DeliveryGateError("INVALID_NONCE")


@dataclass(frozen=True)
class VerifiedReceiver:
    """Host verifier's result; never construct this from a model ACK body."""

    principal: str
    session_id: str
    proof_id: str
    message_id: str
    source_commit: str
    content_sha256: str
    recipient: str
    subject: str
    privacy_scope: str
    nonce: str

    def __post_init__(self) -> None:
        for name in ("principal", "session_id", "proof_id"):
            _require_id(name, getattr(self, name))


class ReceiverProofVerifier(Protocol):
    def verify(self, event: object, binding: DeliveryBinding) -> VerifiedReceiver | None: ...


@dataclass(frozen=True)
class ReceiverDeliveryReceipt:
    message_id: str
    recipient: str
    source_commit: str
    content_sha256: str
    principal: str
    proof_id: str
    committed_at: str
    assurance: str = "AUTHENTICATED_RECEIVER_ACK_ONLY"
    model_response_verified: bool = False


class SqliteReceiverLedger:
    """Unique durable ACK claims; the caller controls a private trusted path.

    This ledger does not authenticate its own caller, establish data protection,
    or prove a model response. Call only from the host-gated path.
    """

    def __init__(self, path: str | Path) -> None:
        if str(path) == ":memory:":
            raise DeliveryGateError("DURABLE_LEDGER_REQUIRED")
        self.connection = sqlite3.connect(str(path), timeout=5.0)
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS authenticated_receiver_acks (
                recipient TEXT NOT NULL,
                message_id TEXT NOT NULL,
                source_commit TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                nonce TEXT NOT NULL UNIQUE,
                principal TEXT NOT NULL,
                session_id TEXT NOT NULL,
                proof_id TEXT NOT NULL UNIQUE,
                subject TEXT NOT NULL,
                privacy_scope TEXT NOT NULL,
                committed_at TEXT NOT NULL,
                PRIMARY KEY (recipient, message_id)
            )"""
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def _record_once(
        self, binding: DeliveryBinding, proof: VerifiedReceiver
    ) -> str:
        """Persist a low-level row; only the verified gate may mint a receipt.

        This is an internal storage operation, not receiver authentication.
        Callers in the host's Python trust domain can still tamper with SQLite,
        so runtime isolation and path ACLs remain mandatory.
        """
        committed_at = datetime.now(timezone.utc).isoformat()
        try:
            with self.connection:
                self.connection.execute(
                    """INSERT INTO authenticated_receiver_acks (
                        recipient, message_id, source_commit, content_sha256,
                        nonce, principal, session_id, proof_id, subject,
                        privacy_scope, committed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        binding.recipient, binding.message_id, binding.source_commit,
                        binding.content_sha256, binding.nonce, proof.principal,
                        proof.session_id, proof.proof_id, binding.subject,
                        binding.privacy_scope, committed_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DeliveryGateError("DURABLE_REPLAY_REJECTED") from exc
        return committed_at


class AuthenticatedReceiverGate:
    """Enforce source -> receiver authentication -> authorization -> commit."""

    def __init__(
        self,
        *,
        canonical_source_verifier: Callable[[DeliveryBinding], bool],
        receiver_proof_verifier: ReceiverProofVerifier,
        receiver_authorizer: Callable[[VerifiedReceiver, DeliveryBinding], bool],
        ledger: SqliteReceiverLedger,
    ) -> None:
        self.canonical_source_verifier = canonical_source_verifier
        self.receiver_proof_verifier = receiver_proof_verifier
        self.receiver_authorizer = receiver_authorizer
        self.ledger = ledger

    def acknowledge(
        self, binding: DeliveryBinding, *, provider_event: object
    ) -> ReceiverDeliveryReceipt:
        # Neither a matching plaintext ACK nor a browser URL is a credential.
        try:
            source_ok = self.canonical_source_verifier(binding) is True
        except Exception:
            source_ok = False
        if not source_ok:
            raise DeliveryGateError("CANONICAL_SOURCE_UNVERIFIED")

        try:
            proof = self.receiver_proof_verifier.verify(provider_event, binding)
        except Exception:
            proof = None
        if not isinstance(proof, VerifiedReceiver):
            raise DeliveryGateError("RECEIVER_AUTH_REQUIRED")

        for name in (
            "message_id", "source_commit", "content_sha256", "recipient",
            "subject", "privacy_scope", "nonce",
        ):
            if getattr(proof, name) != getattr(binding, name):
                raise DeliveryGateError(f"RECEIVER_PROOF_{name.upper()}_MISMATCH")

        try:
            authorized = self.receiver_authorizer(proof, binding) is True
        except Exception:
            authorized = False
        if not authorized:
            raise DeliveryGateError("RECEIVER_NOT_AUTHORIZED")

        committed_at = self.ledger._record_once(binding, proof)
        return ReceiverDeliveryReceipt(
            message_id=binding.message_id,
            recipient=binding.recipient,
            source_commit=binding.source_commit,
            content_sha256=binding.content_sha256,
            principal=proof.principal,
            proof_id=proof.proof_id,
            committed_at=committed_at,
        )


__all__ = [
    "AuthenticatedReceiverGate", "DeliveryBinding", "DeliveryGateError",
    "ReceiverDeliveryReceipt", "ReceiverProofVerifier", "SqliteReceiverLedger",
    "VerifiedReceiver",
]
