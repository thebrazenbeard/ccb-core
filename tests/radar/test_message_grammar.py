import json
from pathlib import Path

import pytest

from radar.message_grammar import MessageGrammarError, parse_bus_document


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "bus_message_grammar_v1.json"


def test_invalid_boolean_is_rejected_by_canonical_grammar() -> None:
    content = b"message_id: radar-9999\nwriter: Radar\nrequires_reply: maybe\n\nBody\n"
    with pytest.raises(MessageGrammarError, match="INVALID_REQUIRES_REPLY"):
        parse_bus_document(content, path="messages/radar-9999.md")


def test_timezone_less_datetime_is_rejected_but_date_only_is_canonicalized() -> None:
    with pytest.raises(MessageGrammarError, match="INVALID_CREATED_AT"):
        parse_bus_document(
            b"message_id: radar-9999\nwriter: Radar\ncreated_at: 2026-09-06T09:00:00\n\nBody\n"
        )

    parsed = parse_bus_document(
        b"message_id: radar-9999\nwriter: Radar\ncreated_at: 2026-09-06\n\nBody\n"
    )
    assert parsed.created_at == "2026-09-06T00:00:00Z"


def test_frontmatter_scalars_hashes_and_endthread_are_shared_evidence() -> None:
    content = (
        b"---\n"
        b"message_id: `radar-9999`\n"
        b"writer: 'Radar'\n"
        b"requires_reply: yes\n"
        b"intended_recipient: alpha, One\n"
        b"ignored_list:\n"
        b"  - provenance-only\n"
        b"---\n\n"
        b"Body\n#ENDTHREAD\n"
    )
    parsed = parse_bus_document(content)
    assert parsed.message_id == "radar-9999"
    assert parsed.writer == "Radar"
    assert parsed.requires_reply is True
    assert parsed.requires_ack is False
    assert parsed.conversation_closed_by_sender is True
    assert len(parsed.full_sha256) == 64
    assert len(parsed.body_sha256) == 64
    assert parsed.body_length == len(b"Body\n#ENDTHREAD\n")


def test_plural_recipient_header_is_bounded_legacy_alias() -> None:
    parsed = parse_bus_document(
        b"message_id: radar-0077\nwriter: Radar\nintended_recipients: delta\n\nBody\n"
    )
    assert parsed.intended_recipient == "delta"


def test_singular_and_plural_recipient_headers_conflict() -> None:
    content = (
        b"message_id: radar-9999\n"
        b"writer: Radar\n"
        b"intended_recipient: delta\n"
        b"intended_recipients: alpha\n\n"
        b"Body\n"
    )
    with pytest.raises(MessageGrammarError, match="CONFLICTING_INTENDED_RECIPIENT_HEADERS"):
        parse_bus_document(content)


def test_shared_fixture_corpus_is_executable_conformance_oracle() -> None:
    fixture = json.loads(FIXTURES.read_text())
    assert fixture["contract"] == "BUS_MESSAGE_GRAMMAR_V1"
    for case in fixture["cases"]:
        content = case["document"].encode("utf-8")
        if not case["accept"]:
            with pytest.raises(MessageGrammarError, match=case["error"]):
                parse_bus_document(content, path=f"fixture:{case['id']}")
            continue
        parsed = parse_bus_document(content, path=f"fixture:{case['id']}")
        expected = case["expected"]
        for key, value in expected.items():
            assert getattr(parsed, key) == value, case["id"]


def test_writer_admission_and_projection_both_use_the_shared_grammar() -> None:
    writer_source = (ROOT / "src" / "radar" / "writer_lanes.py").read_text()
    projection_source = (ROOT / "src" / "radar" / "bus_message_projection.py").read_text()
    assert "from .message_grammar import" in writer_source
    assert "parse_bus_document(" in writer_source
    assert "from .message_grammar import" in projection_source
    assert "parse_bus_document(" in projection_source
