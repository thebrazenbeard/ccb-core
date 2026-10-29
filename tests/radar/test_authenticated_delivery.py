"""Read-only synthetic qualification of host-injected receiver ACK gates."""

from dataclasses import replace
import tempfile
from pathlib import Path
import unittest

from radar.authenticated_delivery import (
    AuthenticatedReceiverGate,
    DeliveryBinding,
    DeliveryGateError,
    SqliteReceiverLedger,
    VerifiedReceiver,
)


class FakeHostProofVerifier:
    def __init__(self, trusted_event: object, proof: VerifiedReceiver):
        self.trusted_event = trusted_event
        self.proof = proof

    def verify(self, event: object, binding: DeliveryBinding):
        # This is a controlled synthetic host verifier, not real provider auth.
        return self.proof if event is self.trusted_event else None


class AuthenticatedDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "private_receipts.sqlite3"
        self.binding = DeliveryBinding(
            message_id="vera-canary-001", source_commit="a" * 40,
            content_sha256="b" * 64, recipient="vera-node-1",
            subject="coordination-test", privacy_scope="nonprivate-canary",
            nonce="c" * 32,
        )
        self.proof = VerifiedReceiver(
            principal="receiver-vera-1", session_id="host-session-1",
            proof_id="provider-proof-1", **vars(self.binding),
        )
        self.trusted_event = object()

    def make_gate(self, ledger, *, source_ok=True, authorized=True):
        return AuthenticatedReceiverGate(
            canonical_source_verifier=lambda _: source_ok,
            receiver_proof_verifier=FakeHostProofVerifier(self.trusted_event, self.proof),
            receiver_authorizer=lambda *_: authorized,
            ledger=ledger,
        )

    def test_forged_plaintext_ack_without_authenticated_session_is_denied(self):
        """Even exact public envelope fields cannot authenticate a receiver."""
        ledger = SqliteReceiverLedger(self.path)
        self.addCleanup(ledger.close)
        forged_ack_text = (
            "BT2_WORKSTATION_RELAY_ACK_V1\n"
            "message_id: vera-canary-001\n"
            + "nonce: " + self.binding.nonce + "\n"
            + "body_sha256: " + self.binding.content_sha256 + "\n"
            + "status: RECEIVED_VERIFIED"
        )
        with self.assertRaisesRegex(DeliveryGateError, "RECEIVER_AUTH_REQUIRED"):
            self.make_gate(ledger).acknowledge(
                self.binding, provider_event=forged_ack_text,
            )
        self.assertEqual(0, ledger.connection.execute(
            "SELECT COUNT(*) FROM authenticated_receiver_acks"
        ).fetchone()[0])

    def test_authenticated_proof_wrong_source_or_subject_never_commits(self):
        ledger = SqliteReceiverLedger(self.path)
        self.addCleanup(ledger.close)
        for altered in (
            replace(self.binding, content_sha256="d" * 64),
            replace(self.binding, source_commit="e" * 40),
            replace(self.binding, recipient="attacker"),
            replace(self.binding, subject="private-person"),
            replace(self.binding, privacy_scope="private"),
        ):
            with self.subTest(field=altered):
                with self.assertRaisesRegex(DeliveryGateError, "RECEIVER_PROOF_.*_MISMATCH"):
                    self.make_gate(ledger).acknowledge(
                        altered, provider_event=self.trusted_event,
                    )
        self.assertEqual(0, ledger.connection.execute(
            "SELECT COUNT(*) FROM authenticated_receiver_acks"
        ).fetchone()[0])

    def test_source_and_authorization_are_separate_fail_closed_gates(self):
        ledger = SqliteReceiverLedger(self.path)
        self.addCleanup(ledger.close)
        with self.assertRaisesRegex(DeliveryGateError, "CANONICAL_SOURCE_UNVERIFIED"):
            self.make_gate(ledger, source_ok=False).acknowledge(
                self.binding, provider_event=self.trusted_event,
            )
        with self.assertRaisesRegex(DeliveryGateError, "RECEIVER_NOT_AUTHORIZED"):
            self.make_gate(ledger, authorized=False).acknowledge(
                self.binding, provider_event=self.trusted_event,
            )

    def test_durable_replay_across_restart_cannot_assert_model_response(self):
        first = SqliteReceiverLedger(self.path)
        receipt = self.make_gate(first).acknowledge(
            self.binding, provider_event=self.trusted_event,
        )
        self.assertEqual("AUTHENTICATED_RECEIVER_ACK_ONLY", receipt.assurance)
        self.assertFalse(receipt.model_response_verified)
        first.close()

        reopened = SqliteReceiverLedger(self.path)
        self.addCleanup(reopened.close)
        with self.assertRaisesRegex(DeliveryGateError, "DURABLE_REPLAY_REJECTED"):
            self.make_gate(reopened).acknowledge(
                self.binding, provider_event=self.trusted_event,
            )
        retry = replace(self.binding, nonce="f" * 32)
        # Even a separately authenticated reissue cannot re-ack the same
        # canonical message after restart; the message key is unique.
        second_proof = replace(
            self.proof, nonce=retry.nonce, proof_id="provider-proof-2"
        )
        retry_gate = AuthenticatedReceiverGate(
            canonical_source_verifier=lambda _: True,
            receiver_proof_verifier=FakeHostProofVerifier(
                self.trusted_event, second_proof
            ),
            receiver_authorizer=lambda *_: True,
            ledger=reopened,
        )
        with self.assertRaisesRegex(DeliveryGateError, "DURABLE_REPLAY_REJECTED"):
            retry_gate.acknowledge(retry, provider_event=self.trusted_event)
        self.assertEqual(1, reopened.connection.execute(
            "SELECT COUNT(*) FROM authenticated_receiver_acks"
        ).fetchone()[0])

    def test_same_provider_proof_cannot_ack_another_message(self):
        ledger = SqliteReceiverLedger(self.path)
        self.addCleanup(ledger.close)
        self.make_gate(ledger).acknowledge(
            self.binding, provider_event=self.trusted_event,
        )
        forged = replace(
            self.binding, message_id="vera-canary-002", nonce="d" * 32,
        )
        with self.assertRaisesRegex(DeliveryGateError, "RECEIVER_PROOF_.*_MISMATCH"):
            self.make_gate(ledger).acknowledge(
                forged, provider_event=self.trusted_event,
            )

    def test_in_memory_ledger_cannot_be_used_as_durable_proof(self):
        with self.assertRaisesRegex(DeliveryGateError, "DURABLE_LEDGER_REQUIRED"):
            SqliteReceiverLedger(":memory:")


if __name__ == "__main__":
    unittest.main()
