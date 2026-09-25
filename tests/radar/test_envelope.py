import importlib
import pytest


def _module():
    try:
        return importlib.import_module("radar.envelope")
    except Exception as exc:
        pytest.fail(f"Radar envelope implementation missing: {exc}")


def test_valid_envelope_has_deterministic_content_hash():
    m = _module()
    raw = {"schema_version": 1, "message_id": "m1", "created_at": "2026-09-02T12:00:00Z", "sender": "alpha", "audience": ["radar"], "domain": "coordination", "intent": "event", "priority": 2, "payload": {"b": 2, "a": 1}}
    a = m.RadarEnvelope.from_mapping(raw)
    b = m.RadarEnvelope.from_mapping({**raw, "payload": {"a": 1, "b": 2}})
    assert a.classification is m.EnvelopeClassification.VALID
    assert a.envelope.content_hash == b.envelope.content_hash
    assert len(a.envelope.content_hash) == 64


def test_hash_binds_sender_domain_and_intent_not_payload_only():
    m = _module()
    base = {"schema_version": 1, "message_id": "m1", "created_at": "2026-09-02T12:00:00Z", "sender": "alpha", "audience": ["radar"], "domain": "coordination", "intent": "event", "priority": 2, "payload": {"x": 1}}
    h1 = m.RadarEnvelope.from_mapping(base).envelope.content_hash
    h2 = m.RadarEnvelope.from_mapping({**base, "sender": "yang"}).envelope.content_hash
    assert h1 != h2


def test_missing_required_sender_is_rejected_not_anonymous():
    m = _module()
    result = m.RadarEnvelope.from_mapping({"schema_version": 1, "domain": "system", "intent": "event", "payload": {}})
    assert result.classification is m.EnvelopeClassification.REJECTED
    assert "sender" in result.errors


def test_safe_defaults_are_explicitly_normalized():
    m = _module()
    result = m.RadarEnvelope.from_mapping({"schema_version": 1, "sender": "alpha", "domain": "system", "intent": "telemetry", "payload": {}})
    assert result.classification is m.EnvelopeClassification.NORMALIZED
    assert result.envelope.priority == 3
    assert result.envelope.message_id.startswith("rad_")


@pytest.mark.parametrize("value", [True, 1.0, 1.9, "1"])
def test_non_integer_schema_versions_are_rejected_without_coercion(value):
    m = _module()
    base = {
        "schema_version": 1,
        "message_id": "m1",
        "created_at": "2026-09-02T12:00:00Z",
        "sender": "alpha",
        "audience": ["radar"],
        "domain": "coordination",
        "intent": "event",
        "priority": 2,
        "payload": {},
    }
    result = m.RadarEnvelope.from_mapping({**base, "schema_version": value})
    assert result.classification is m.EnvelopeClassification.REJECTED
    assert result.errors == ("schema_version",)


@pytest.mark.parametrize("value", [None, True, 1.0, 1.9, "1"])
def test_non_integer_priorities_are_rejected_without_coercion(value):
    m = _module()
    base = {
        "schema_version": 1,
        "message_id": "m1",
        "created_at": "2026-09-02T12:00:00Z",
        "sender": "alpha",
        "audience": ["radar"],
        "domain": "coordination",
        "intent": "event",
        "priority": 2,
        "payload": {},
    }
    result = m.RadarEnvelope.from_mapping({**base, "priority": value})
    assert result.classification is m.EnvelopeClassification.REJECTED
    assert result.errors == ("priority",)


def test_omitted_priority_defaults_but_explicit_null_rejects():
    m = _module()
    base = {
        "schema_version": 1,
        "message_id": "m1",
        "created_at": "2026-09-02T12:00:00Z",
        "sender": "alpha",
        "audience": ["radar"],
        "domain": "coordination",
        "intent": "event",
        "payload": {},
    }

    omitted = m.RadarEnvelope.from_mapping(base)
    explicit_null = m.RadarEnvelope.from_mapping({**base, "priority": None})

    assert omitted.classification is m.EnvelopeClassification.NORMALIZED
    assert omitted.envelope.priority == m.DEFAULT_PRIORITY
    assert explicit_null.classification is m.EnvelopeClassification.REJECTED
    assert explicit_null.errors == ("priority",)


def test_source_refs_reject_empty_or_whitespace_entries():
    m = _module()
    raw = {
        "schema_version": 1,
        "message_id": "m1",
        "created_at": "2026-09-02T12:00:00Z",
        "sender": "alpha",
        "audience": ["radar"],
        "domain": "coordination",
        "intent": "event",
        "priority": 2,
        "payload": {},
        "source_refs": ["github://valid", "   "],
    }
    result = m.RadarEnvelope.from_mapping(raw)
    assert result.classification is m.EnvelopeClassification.REJECTED
    assert result.errors == ("source_refs",)
