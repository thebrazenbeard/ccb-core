"""Canonical byte-level Bus message grammar shared by admission/projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import re
from typing import Literal


_HEADER_RE = re.compile(r"^(?P<key>[A-Za-z0-9_-]+)\s*:\s*(?P<value>.*)$")
_TRUE = {"true", "yes", "1"}
_FALSE = {"false", "no", "0"}
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class MessageGrammarError(ValueError):
    """Raised when Bus message bytes violate BUS_MESSAGE_GRAMMAR_V1."""


@dataclass(frozen=True)
class ParsedBusDocument:
    headers: dict[str, str]
    body: str
    message_id: str
    writer: str
    created_at: str | None
    created_at_invalid: bool
    requires_reply: bool
    requires_ack: bool
    intended_recipient: str
    subject: str | None
    full_sha256: str
    body_sha256: str
    body_length: int
    conversation_closed_by_sender: bool


def unwrap_scalar(value: str) -> str:
    text = value.strip()
    for left, right in (("`", "`"), ('"', '"'), ("'", "'")):
        if len(text) >= 2 and text.startswith(left) and text.endswith(right):
            return text[1:-1].strip()
    return text


def parse_bus_bool(value: str | None, *, field: str) -> bool:
    if value is None or value.strip() == "":
        return False
    normalized = unwrap_scalar(value).casefold()
    if normalized in _TRUE:
        return True
    if normalized in _FALSE:
        return False
    raise MessageGrammarError(f"INVALID_{field.upper()}")


def normalize_bus_timestamp(value: str, *, field: str = "created_at") -> str:
    text = unwrap_scalar(value)
    if not text:
        raise MessageGrammarError(f"INVALID_{field.upper()}")
    if _DATE_ONLY_RE.fullmatch(text):
        candidate = f"{text}T00:00:00Z"
        try:
            datetime.fromisoformat(candidate[:-1] + "+00:00")
        except ValueError as exc:
            raise MessageGrammarError(f"INVALID_{field.upper()}") from exc
        return candidate

    iso_candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(iso_candidate)
    except ValueError as exc:
        raise MessageGrammarError(f"INVALID_{field.upper()}") from exc
    if parsed.tzinfo is None:
        raise MessageGrammarError(f"INVALID_{field.upper()}")
    return text


def _put_header(headers: dict[str, str], raw: str) -> bool:
    match = _HEADER_RE.match(raw.rstrip("\r\n"))
    if not match:
        return False
    key = match.group("key").strip().casefold().replace("-", "_")
    if key in headers:
        raise MessageGrammarError(f"DUPLICATE_HEADER_{key.upper()}")
    headers[key] = unwrap_scalar(match.group("value"))
    return True


def _split_frontmatter(lines: list[str]) -> tuple[dict[str, str], int] | None:
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines) or lines[index].strip() != "---":
        return None

    headers: dict[str, str] = {}
    index += 1
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        if stripped == "---":
            index += 1
            while index < len(lines) and not lines[index].strip():
                index += 1
            if not headers:
                raise MessageGrammarError("HEADER_BLOCK_REQUIRED")
            return headers, index
        # YAML lists/continuations are provenance, not scalar message headers.
        if raw[:1].isspace() or stripped.startswith("-"):
            index += 1
            continue
        if stripped and not _put_header(headers, raw):
            raise MessageGrammarError("INVALID_FRONTMATTER_HEADER")
        index += 1
    raise MessageGrammarError("UNTERMINATED_FRONTMATTER")


def _split_document(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines(keepends=True)
    frontmatter = _split_frontmatter(lines)
    if frontmatter is not None:
        headers, body_index = frontmatter
        return headers, "".join(lines[body_index:])

    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index < len(lines) and lines[index].lstrip().startswith("#"):
        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1

    headers: dict[str, str] = {}
    started = False
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            if started:
                index += 1
                break
            index += 1
            continue
        if not _put_header(headers, line):
            if not started:
                raise MessageGrammarError("HEADER_BLOCK_REQUIRED")
            break
        started = True
        index += 1

    if not started:
        raise MessageGrammarError("HEADER_BLOCK_REQUIRED")
    return headers, "".join(lines[index:])


def _closed_by_sender(body: str) -> bool:
    trimmed = body.rstrip()
    return bool(trimmed) and trimmed.splitlines()[-1] == "#ENDTHREAD"


def _intended_recipient(headers: dict[str, str]) -> str:
    singular = headers.get("intended_recipient")
    plural = headers.get("intended_recipients")
    if singular is not None and plural is not None:
        raise MessageGrammarError("CONFLICTING_INTENDED_RECIPIENT_HEADERS")
    return singular if singular is not None else plural or ""


def parse_bus_document(
    content: bytes,
    *,
    path: str = "<memory>",
    timestamp_policy: Literal["strict", "source_fallback"] = "strict",
) -> ParsedBusDocument:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MessageGrammarError(f"MESSAGE_NOT_UTF8:{path}") from exc

    headers, body = _split_document(text)
    message_id = headers.get("message_id", "")
    if not message_id:
        raise MessageGrammarError("MESSAGE_ID_REQUIRED")
    writer = headers.get("writer", "")
    if not writer:
        raise MessageGrammarError("WRITER_REQUIRED")

    created_at: str | None = None
    created_at_invalid = False
    created_raw = headers.get("created_at")
    if created_raw:
        try:
            created_at = normalize_bus_timestamp(created_raw)
        except MessageGrammarError:
            if timestamp_policy != "source_fallback":
                raise
            created_at_invalid = True

    requires_reply = parse_bus_bool(headers.get("requires_reply"), field="requires_reply")
    requires_ack = parse_bus_bool(headers.get("requires_ack"), field="requires_ack")
    body_bytes = body.encode("utf-8")
    subject = headers.get("subject") or None

    return ParsedBusDocument(
        headers=headers,
        body=body,
        message_id=message_id,
        writer=writer,
        created_at=created_at,
        created_at_invalid=created_at_invalid,
        requires_reply=requires_reply,
        requires_ack=requires_ack,
        intended_recipient=_intended_recipient(headers),
        subject=subject,
        full_sha256=hashlib.sha256(content).hexdigest(),
        body_sha256=hashlib.sha256(body_bytes).hexdigest(),
        body_length=len(body_bytes),
        conversation_closed_by_sender=_closed_by_sender(body),
    )


__all__ = [
    "MessageGrammarError",
    "ParsedBusDocument",
    "normalize_bus_timestamp",
    "parse_bus_bool",
    "parse_bus_document",
    "unwrap_scalar",
]
