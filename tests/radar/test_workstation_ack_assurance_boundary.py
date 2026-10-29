"""Negative assurance controls for sender-visible workstation ACK text.

These tests intentionally do not claim a live browser or authenticated Bus.
"""

import unittest
from types import SimpleNamespace

from radar.workstation_relay import _parse_ack, render_ack


class AckAssuranceBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.envelope = SimpleNamespace(
            message_id="canary-ack-assurance-001",
            nonce="0123456789abcdef0123456789abcdef",
            body_sha256="a" * 64,
        )

    def test_any_holder_of_public_envelope_fields_can_construct_matching_ack(self) -> None:
        # No browser, Bus row, credential, receiver identity, or model is used.
        synthetic_text = render_ack(self.envelope, status="RECEIVED_VERIFIED")
        self.assertEqual(_parse_ack(self.envelope, synthetic_text), "RECEIVED_VERIFIED")
        self.assertNotIn("receiver_principal:", synthetic_text)
        self.assertNotIn("receiver_signature:", synthetic_text)

    def test_nonce_or_body_digest_mismatch_is_rejected_but_not_authentication(self) -> None:
        synthetic_text = render_ack(self.envelope, status="RECEIVED_VERIFIED")
        stale = SimpleNamespace(
            message_id=self.envelope.message_id,
            nonce="f" * 32,
            body_sha256=self.envelope.body_sha256,
        )
        wrong_body = SimpleNamespace(
            message_id=self.envelope.message_id,
            nonce=self.envelope.nonce,
            body_sha256="b" * 64,
        )
        self.assertEqual(_parse_ack(stale, synthetic_text), "MISMATCH")
        self.assertEqual(_parse_ack(wrong_body, synthetic_text), "MISMATCH")

    def test_source_unverified_hold_text_is_not_a_positive_ack(self) -> None:
        hold_text = render_ack(self.envelope, status="HOLD_SOURCE_UNVERIFIED")
        self.assertEqual(_parse_ack(self.envelope, hold_text), "HOLD_SOURCE_UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
