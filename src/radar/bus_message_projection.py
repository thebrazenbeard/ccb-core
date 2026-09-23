"""Parse and project canonical Bus messages from Git repositories."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from .message_grammar import (
    MessageGrammarError,
    normalize_bus_timestamp,
    parse_bus_document,
    unwrap_scalar,
)
from .supabase_projection import ProjectionCommand

logger = logging.getLogger(__name__)


class BusMessageProjectionError(ValueError):
    """Raised when a bus message document cannot be projected safely."""


_ALIASES = {
    "beta-legacy": "beta",
    "alpha/core": "alpha",
    "alpha/core": "alpha",
    "alpha-core": "alpha",
}

_RECOGNIZED_IDENTITIES = {
    "epsilon",
    "radar",
    "beta",
    "five-five",
    "five",
    "four",
    "six",
    "seven",
    "eight",
    "nine",
    "one",
    "two",
    "three",
    "thirteen",
    "zeta",
    "gamma",
    "yang",
    "yin",
    "delta",
}


@dataclass(frozen=True)
class BusMessageSource:
    """Source metadata for a bus message in a Git repository."""

    repository: str
    ref: str
    branch_head: str
    path: str
    source_commit: str
    blob_sha: str
    source_committed_at: str | None = None


@dataclass(frozen=True)
class ProjectedBusMessage:
    """Normalized representation of a bus message ready for storage."""

    message_id: str
    created_at: str
    sender: str
    audience: tuple[str, ...]
    requires_ack: bool
    subject: str | None
    full_sha256: str
    body_sha256: str
    body_length: int
    source: BusMessageSource
    metadata: dict[str, object]


def _normalize_identity(value: str) -> str:
    key = unwrap_scalar(value).strip().casefold()
    if mapped := _ALIASES.get(key):
        return mapped
    if " / " in key:
        prefix = key.split(" / ", 1)[0].strip()
        if prefix in _RECOGNIZED_IDENTITIES:
            return prefix
    return key


def _source_timestamp(value: str) -> str:
    try:
        return normalize_bus_timestamp(value, field="source_committed_at")
    except MessageGrammarError as exc:
        raise BusMessageProjectionError("INVALID_SOURCE_COMMITTED_AT") from exc


def parse_bus_message(content: bytes, source: BusMessageSource) -> ProjectedBusMessage:
    """Parse canonical message bytes and add Git-source projection evidence.

    New writer admission uses strict timestamp grammar. Projection retains the
    bounded historical compatibility rule: an invalid legacy ``created_at``
    header may use the immutable source commit time instead. Boolean/header
    grammar never falls back.
    """
    try:
        parsed = parse_bus_document(
            content,
            path=source.path,
            timestamp_policy="source_fallback",
        )
    except MessageGrammarError as exc:
        raise BusMessageProjectionError(str(exc)) from exc

    if parsed.created_at is not None:
        created_at = parsed.created_at
        created_at_basis = "message_header"
    elif parsed.created_at_invalid:
        if not source.source_committed_at:
            raise BusMessageProjectionError("INVALID_CREATED_AT")
        created_at = _source_timestamp(source.source_committed_at)
        created_at_basis = "source_commit_invalid_header"
    elif source.source_committed_at:
        created_at = _source_timestamp(source.source_committed_at)
        created_at_basis = "source_commit"
    else:
        raise BusMessageProjectionError("CREATED_AT_REQUIRED")

    audience = tuple(
        _normalize_identity(piece)
        for piece in parsed.intended_recipient.split(",")
        if unwrap_scalar(piece)
    )
    requires_ack = parsed.requires_reply or parsed.requires_ack

    metadata: dict[str, object] = dict(parsed.headers)
    metadata.update(
        {
            "created_at_basis": created_at_basis,
            "source_repository": source.repository,
            "source_ref": source.ref,
            "source_branch_head": source.branch_head,
            "source_path": source.path,
            "source_commit": source.source_commit,
            "source_blob_sha": source.blob_sha,
            "body_sha256": parsed.body_sha256,
            "body_length": parsed.body_length,
            "conversation_closed_by_sender": parsed.conversation_closed_by_sender,
        }
    )

    logger.debug(
        "Parsed message %s from %s:%s",
        parsed.message_id,
        source.repository,
        source.path,
    )

    return ProjectedBusMessage(
        message_id=parsed.message_id,
        created_at=created_at,
        sender=_normalize_identity(parsed.writer),
        audience=audience,
        requires_ack=requires_ack,
        subject=parsed.subject,
        full_sha256=parsed.full_sha256,
        body_sha256=parsed.body_sha256,
        body_length=parsed.body_length,
        source=source,
        metadata=metadata,
    )


def build_bus_projection_command(projected: ProjectedBusMessage) -> ProjectionCommand:
    source_refs = (
        f"github://{projected.source.repository}@{projected.source.source_commit}/{projected.source.path}",
        f"github-ref://{projected.source.repository}/{projected.source.ref}@{projected.source.branch_head}",
        f"git-blob:{projected.source.blob_sha}",
    )
    parameters: dict[str, object] = {
        "p_message_id": projected.message_id,
        "p_created_at": projected.created_at,
        "p_sender": projected.sender,
        "p_audience": list(projected.audience),
        "p_requires_ack": projected.requires_ack,
        "p_source_refs": list(source_refs),
        "p_content_hash": projected.full_sha256,
        "p_idempotency_key": f"chat-bus:{projected.message_id}:{projected.full_sha256}",
        "p_payload": dict(projected.metadata),
    }
    return ProjectionCommand(
        operation="PROJECT_BUS_MESSAGE",
        schema="radar",
        sql=(
            "select radar.project_bus_message_v1("
            "%(p_message_id)s, %(p_created_at)s, %(p_sender)s, %(p_audience)s, %(p_requires_ack)s, "
            "%(p_source_refs)s, %(p_content_hash)s, %(p_idempotency_key)s, %(p_payload)s)"
        ),
        parameters=parameters,
    )


__all__ = [
    "BusMessageProjectionError",
    "BusMessageSource",
    "ProjectedBusMessage",
    "parse_bus_message",
    "build_bus_projection_command",
]
