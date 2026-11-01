"""Fail-closed bounds before Radar admission serializes untrusted wire data."""

from __future__ import annotations

from collections.abc import Mapping
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from radar.dlq import DeadLetterStore
from radar.envelope import EnvelopeClassification, RadarEnvelope
import radar.envelope as envelope_module
from radar.registry import IdentityRegistry
from radar.runtime import RadarRuntime
from radar.telemetry import TelemetryStore


def _raw(payload: object, **overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": 1,
        "sender": "radar",
        "domain": "coordination",
        "intent": "event",
        "payload": payload,
        "message_id": "ordinary-identifier",
    }
    result.update(overrides)
    return result


class _DishonestMapping(Mapping):
    """Non-wire Python object that lies about width and must not be iterated."""

    def __init__(self) -> None:
        self.iterations = 0

    def __len__(self) -> int:
        return 1

    def __iter__(self):
        self.iterations += 1
        raise AssertionError("untrusted mapping iteration")

    def __getitem__(self, key):
        raise KeyError(key)


class _DishonestString(str):
    """Subclass that can hide enormous content behind a false length."""

    def __len__(self) -> int:
        return 1


class _DishonestList(list):
    """Container subclass that lies about width and forbids iteration."""

    def __init__(self) -> None:
        super().__init__(range(10_000))
        self.iterations = 0

    def __len__(self) -> int:
        return 1

    def __iter__(self):
        self.iterations += 1
        raise AssertionError("untrusted list iteration")


class AdmissionBoundaryTests(unittest.TestCase):
    def _runtime(self, location: Path) -> RadarRuntime:
        dlq = DeadLetterStore(location / "dlq.sqlite")
        telemetry = TelemetryStore(location / "telemetry.sqlite")
        self.addCleanup(dlq.close)
        self.addCleanup(telemetry.close)
        return RadarRuntime(IdentityRegistry(), telemetry=telemetry, dlq=dlq)

    def test_valid_taxonomy_huge_payload_rejects_before_json_serialization(self) -> None:
        giant = "x" * (2 * 1024 * 1024)
        raw = _raw(giant)
        original_dumps = envelope_module.json.dumps
        observed = []

        def guarded_dumps(value, *args, **kwargs):
            if value is giant:
                observed.append(True)
                raise AssertionError("serialized giant source payload")
            return original_dumps(value, *args, **kwargs)

        with patch.object(envelope_module.json, "dumps", side_effect=guarded_dumps):
            result = RadarEnvelope.from_mapping(raw)
        self.assertEqual([], observed)
        self.assertIs(result.classification, EnvelopeClassification.REJECTED)
        self.assertIn("payload_budget", result.errors)
        with tempfile.TemporaryDirectory() as root:
            runtime = self._runtime(Path(root))
            receipt = runtime.admit(raw, now_ms=1)
            self.assertEqual("REJECTED", receipt.state)
            self.assertEqual("ordinary-identifier", receipt.message_id)
            self.assertIn("payload_budget", receipt.errors)

    def test_valid_taxonomy_depth_width_and_nonconforming_mapping_fail_closed(self) -> None:
        deep: object = {"leaf": True}
        for _ in range(40):
            deep = {"next": deep}
        for payload in (deep, list(range(5_000)), _DishonestMapping()):
            with self.subTest(payload_type=type(payload).__name__):
                result = RadarEnvelope.from_mapping(_raw(payload))
                self.assertIs(result.classification, EnvelopeClassification.REJECTED)
                self.assertIn("payload_budget", result.errors)
        mapping = _DishonestMapping()
        result = RadarEnvelope.from_mapping(_raw(mapping))
        self.assertEqual(0, mapping.iterations)
        self.assertIs(result.classification, EnvelopeClassification.REJECTED)

    def test_large_explicit_and_whitespace_only_ids_never_escape_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            runtime = self._runtime(Path(root))
            for supplied in ("m" * (512 * 1024), " " * (2 * 1024 * 1024)):
                with self.subTest(kind="nonblank" if supplied[0] == "m" else "blank"):
                    receipt = runtime.admit(_raw({"ordinary": True}, message_id=supplied), now_ms=1)
                    self.assertEqual("REJECTED", receipt.state)
                    self.assertLessEqual(len(receipt.message_id), 128)
                    self.assertIn("message_id", receipt.errors)

    def test_unknown_taxonomy_still_reaches_bounded_dlq_with_long_explicit_id(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            runtime = self._runtime(Path(root))
            supplied = "z" * (512 * 1024)
            receipt = runtime.admit(
                _raw({"value": 1}, domain="not-a-domain", message_id=supplied), now_ms=1
            )
            self.assertEqual("DLQ", receipt.state)
            self.assertRegex(receipt.message_id, r"^radar-dlq-[a-f0-9]{16}$")
            item = runtime.dlq.inspect(receipt.dlq_item_id)
            self.assertEqual("<string-limit>", item.envelope["message_id"])

    def test_custom_top_level_mapping_is_rejected_without_iteration(self) -> None:
        mapping = _DishonestMapping()
        direct = RadarEnvelope.from_mapping(mapping)
        self.assertIs(direct.classification, EnvelopeClassification.REJECTED)
        self.assertEqual(("envelope",), direct.errors)
        with tempfile.TemporaryDirectory() as root:
            runtime = self._runtime(Path(root))
            receipt = runtime.admit(mapping, now_ms=1)
            self.assertEqual("REJECTED", receipt.state)
            self.assertEqual(0, mapping.iterations)

    def test_unknown_taxonomy_list_subclass_is_opaque_without_iteration(self) -> None:
        liar = _DishonestList()
        with tempfile.TemporaryDirectory() as root:
            runtime = self._runtime(Path(root))
            receipt = runtime.admit(
                _raw(liar, domain="not-a-domain"), now_ms=1
            )
            self.assertEqual("DLQ", receipt.state)
            self.assertEqual(0, liar.iterations)
            item = runtime.dlq.inspect(receipt.dlq_item_id)
            self.assertEqual("<_DishonestList>", item.envelope["payload"])

    def test_small_normal_envelope_remains_valid_and_hash_stable(self) -> None:
        raw = _raw({"b": 2, "a": [1, "two"]})
        first = RadarEnvelope.from_mapping(raw)
        second = RadarEnvelope.from_mapping(dict(raw))
        self.assertIs(first.classification, EnvelopeClassification.NORMALIZED)
        self.assertEqual(first.envelope.content_hash, second.envelope.content_hash)
        self.assertEqual(first.envelope.idempotency_key, second.envelope.idempotency_key)

    def test_oversized_audience_and_source_refs_reject_before_normalization(self) -> None:
        for field in ("audience", "source_refs"):
            raw = _raw({"value": True}, **{field: ["a" * 262_144]})
            with self.subTest(field=field):
                result = RadarEnvelope.from_mapping(raw)
                self.assertIs(result.classification, EnvelopeClassification.REJECTED)
                self.assertIn("envelope_budget", result.errors)

    def test_aggregate_metadata_cannot_exceed_wire_budget(self) -> None:
        raw = _raw(
            {"value": True},
            audience=["a" * 4_096] * 128,
            source_refs=["s" * 4_096] * 128,
        )
        result = RadarEnvelope.from_mapping(raw)
        self.assertIs(result.classification, EnvelopeClassification.REJECTED)
        self.assertIn("envelope_budget", result.errors)

    def test_string_subclass_cannot_spoof_message_id_length(self) -> None:
        dishonest = _DishonestString("s" * 131_072)
        valid = RadarEnvelope.from_mapping(_raw({"value": True}, message_id=dishonest))
        self.assertIs(valid.classification, EnvelopeClassification.REJECTED)
        with tempfile.TemporaryDirectory() as root:
            runtime = self._runtime(Path(root))
            receipt = runtime.admit(
                _raw({"value": True}, domain="not-a-domain", message_id=dishonest),
                now_ms=1,
            )
            self.assertEqual("DLQ", receipt.state)
            self.assertLessEqual(len(receipt.message_id), 128)
            item = runtime.dlq.inspect(receipt.dlq_item_id)
            self.assertLessEqual(len(item.message_id), 128)

    def test_source_locator_is_bounded_before_dead_letter_write(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            runtime = self._runtime(Path(root))
            for locator in (
                "l" * 262_144,
                _DishonestString("l" * 262_144),
                " ",
                "\t\n",
                "",
            ):
                with self.subTest(locator_type=type(locator).__name__):
                    receipt = runtime.admit(
                        _raw({"value": True}, domain="not-a-domain"),
                        now_ms=1,
                        source_locator=locator,
                    )
                    self.assertEqual("REJECTED", receipt.state)
                    self.assertIn("source_locator", receipt.errors)
                    self.assertIsNone(receipt.dlq_item_id)
                    self.assertEqual([], runtime.dlq.list_pending_applicable())


if __name__ == "__main__":
    unittest.main()
