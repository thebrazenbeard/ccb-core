"""Source regressions for bounded synthetic Radar dead-letter identities."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from radar.dlq import DeadLetterStore
from radar.registry import IdentityRegistry
import radar.runtime as runtime_module
from radar.runtime import RadarRuntime
from radar.telemetry import TelemetryStore


def _without_message_id(payload: object) -> dict[str, object]:
    return {
        "schema_version": 1,
        "sender": "radar",
        "domain": "not-a-domain",
        "intent": "event",
        "payload": payload,
    }


class BoundedMessageIdTests(unittest.TestCase):
    def test_missing_id_never_serializes_unbounded_raw_mapping(self) -> None:
        raw = _without_message_id("x" * (8 * 1024 * 1024))
        original_dumps = runtime_module.json.dumps
        serialized_raw = []

        def auditing_dumps(value, *args, **kwargs):
            if value is raw:
                serialized_raw.append(True)
            return original_dumps(value, *args, **kwargs)

        with patch.object(runtime_module.json, "dumps", side_effect=auditing_dumps):
            identifier = RadarRuntime._raw_message_id(raw)

        self.assertEqual([], serialized_raw)
        self.assertRegex(identifier, r"^radar-dlq-[a-f0-9]{16}$")

    def test_missing_id_stably_identifies_bounded_dead_letter(self) -> None:
        raw = _without_message_id("x" * (8 * 1024 * 1024))
        with tempfile.TemporaryDirectory() as root:
            location = Path(root)
            dlq = DeadLetterStore(location / "dlq.sqlite")
            telemetry = TelemetryStore(location / "telemetry.sqlite")
            try:
                runtime = RadarRuntime(IdentityRegistry(), telemetry=telemetry, dlq=dlq)
                first = runtime.admit(raw, now_ms=1)
                second = runtime.admit(raw, now_ms=2)
                self.assertEqual("DLQ", first.state)
                self.assertEqual("DLQ", second.state)
                self.assertEqual(first.message_id, second.message_id)
                self.assertEqual("<string-limit>", dlq.inspect(first.dlq_item_id).envelope["payload"])
                self.assertEqual(2, telemetry.snapshot().dlq)
            finally:
                dlq.close()
                telemetry.close()

    def test_distinct_oversized_payloads_have_distinct_durable_row_ids(self) -> None:
        """Synthetic projection IDs may collide; SQLite item IDs must not."""
        first_raw = _without_message_id("x" * 5_000)
        second_raw = _without_message_id("y" * 5_000)
        with tempfile.TemporaryDirectory() as root:
            location = Path(root)
            dlq = DeadLetterStore(location / "dlq.sqlite")
            telemetry = TelemetryStore(location / "telemetry.sqlite")
            try:
                runtime = RadarRuntime(IdentityRegistry(), telemetry=telemetry, dlq=dlq)
                first = runtime.admit(first_raw, now_ms=1)
                second = runtime.admit(second_raw, now_ms=2)
                self.assertEqual("DLQ", first.state)
                self.assertEqual("DLQ", second.state)
                # Bounded diagnostic IDs describe the same sanitized projection,
                # not the distinct original input bytes.
                self.assertEqual(first.message_id, second.message_id)
                self.assertNotEqual(first.dlq_item_id, second.dlq_item_id)
                first_item = dlq.inspect(first.dlq_item_id)
                second_item = dlq.inspect(second.dlq_item_id)
                self.assertEqual(first.message_id, first_item.message_id)
                self.assertEqual(second.message_id, second_item.message_id)
                self.assertEqual("<string-limit>", first_item.envelope["payload"])
                self.assertEqual("<string-limit>", second_item.envelope["payload"])
            finally:
                dlq.close()
                telemetry.close()

    def test_missing_id_distinguishes_bounded_content_and_handles_cycles(self) -> None:
        self.assertNotEqual(
            RadarRuntime._raw_message_id(_without_message_id({"value": 1})),
            RadarRuntime._raw_message_id(_without_message_id({"value": 2})),
        )
        cycle: dict[str, object] = {}
        cycle["self"] = cycle
        raw = _without_message_id(cycle)
        self.assertEqual(
            RadarRuntime._raw_message_id(raw), RadarRuntime._raw_message_id(raw)
        )

    def test_small_json_fallback_preserves_existing_deterministic_id(self) -> None:
        raw = _without_message_id({"value": "ordinary", "count": 2})
        canonical = json.dumps(
            raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        expected = f"radar-dlq-{hashlib.sha256(canonical).hexdigest()[:16]}"
        self.assertEqual(expected, RadarRuntime._raw_message_id(raw))


if __name__ == "__main__":
    unittest.main()
