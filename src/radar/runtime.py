"""Canonical in-process Radar admission, queueing, routing, and accounting path."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
import threading
from typing import Mapping

from .dlq import DeadLetterStore
from .envelope import EnvelopeClassification, RadarEnvelope
from .registry import IdentityRegistry
from .routing import DedupeWindow, PriorityQueue, Router
from .telemetry import TelemetryStore


@dataclass(frozen=True)
class RuntimeReceipt:
    state: str
    message_id: str
    recipients: tuple[str, ...] = ()
    dlq_item_id: int | None = None
    evicted_message_id: str | None = None
    errors: tuple[str, ...] = ()


@dataclass
class _DlqChanges:
    """Record sanitizer actions independently of literal payload strings."""

    entries: list[dict[str, str]] = field(default_factory=list)
    omitted: int = 0

    def mark(self, path: str, reason: str) -> None:
        if len(self.entries) < 64:
            self.entries.append({"path": path, "reason": reason})
        else:
            self.omitted += 1

    def sidecar(self) -> dict[str, object]:
        result: dict[str, object] = {
            "schema": "RADAR_DLQ_SANITIZATION_V1",
            "changes": self.entries,
        }
        if self.omitted:
            result["omitted_changes"] = self.omitted
        return result


class RadarRuntime:
    """Join Radar's admission, queueing, routing, and accounting seams."""

    _MAX_DLQ_DEPTH = 24
    _MAX_DLQ_WIDTH = 128
    _MAX_DLQ_NODES = 2_048
    _MAX_DLQ_STRING_CHARS = 4_096
    _MAX_DLQ_JSON_BYTES = 65_536

    @staticmethod
    def _dlq_pointer_segment(segment: str) -> str:
        return segment.replace("~", "~0").replace("/", "~1")

    @staticmethod
    def _dlq_root_fallback(reason: str) -> dict[str, object]:
        changes = _DlqChanges()
        changes.mark("", reason)
        return {"_radar_dlq_sanitization": changes.sidecar()}

    def __init__(
        self,
        registry: IdentityRegistry,
        *,
        telemetry: TelemetryStore,
        dlq: DeadLetterStore,
        queue_size: int = 1024,
        dedupe_window_ms: int = 500,
    ) -> None:
        self.registry = registry
        self.telemetry = telemetry
        self.dlq = dlq
        self.router = Router(registry)
        self.queue = PriorityQueue(max_size=queue_size)
        self.dedupe = DedupeWindow(window_ms=dedupe_window_ms)
        self._admission_lock = threading.RLock()

    @staticmethod
    def _dedupe_key(envelope: RadarEnvelope) -> str:
        """Return the V1 middleware payload hash exactly as the dedupe key."""
        return envelope.idempotency_key

    @staticmethod
    def _raw_message_id(raw: Mapping[str, object]) -> str:
        value = raw.get("message_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
        try:
            canonical = json.dumps(
                raw,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                default=lambda value: f"<{type(value).__name__}>",
            ).encode("utf-8")
        except Exception:
            canonical = repr(sorted((str(key), type(value).__name__) for key, value in raw.items())).encode(
                "utf-8", errors="replace"
            )
        return f"radar-dlq-{hashlib.sha256(canonical).hexdigest()[:16]}"

    @classmethod
    def _safe_dlq_value(
        cls,
        value: object,
        *,
        depth: int = 0,
        ancestors: set[int] | None = None,
        node_budget: list[int] | None = None,
        changes: _DlqChanges | None = None,
        path: str = "",
    ) -> object:
        def replacement(reason: str, marker: str) -> str:
            if changes is not None:
                changes.mark(path, reason)
            return marker

        if node_budget is None:
            node_budget = [cls._MAX_DLQ_NODES]
        if node_budget[0] <= 0:
            return replacement("NODE_LIMIT", "<node-limit>")
        node_budget[0] -= 1
        if depth >= cls._MAX_DLQ_DEPTH:
            return replacement("DEPTH_LIMIT", "<depth-limit>")
        if isinstance(value, str) and len(value) > cls._MAX_DLQ_STRING_CHARS:
            return replacement("STRING_LIMIT", "<string-limit>")
        if value is None or isinstance(value, (str, bool, int, float)):
            return value
        if isinstance(value, (Mapping, list, tuple)):
            if len(value) > cls._MAX_DLQ_WIDTH:
                return replacement("WIDTH_LIMIT", "<width-limit>")
            if ancestors is None:
                ancestors = set()
            marker = id(value)
            if marker in ancestors:
                return replacement("CYCLE", "<cycle>")
            ancestors.add(marker)
            try:
                if isinstance(value, Mapping):
                    # JSON object keys are strings. Stringifying arbitrary keys
                    # can silently merge distinct evidence (1 versus "1").
                    if any(type(key) is not str for key in value):
                        return replacement("INVALID_MAP_KEYS", "<invalid-map-keys>")
                    if any(len(key) > cls._MAX_DLQ_STRING_CHARS for key in value):
                        return replacement("KEY_LIMIT", "<key-limit>")
                    return {
                        key: cls._safe_dlq_value(
                            item,
                            depth=depth + 1,
                            ancestors=ancestors,
                            node_budget=node_budget,
                            changes=changes,
                            path=path + "/" + cls._dlq_pointer_segment(key),
                        )
                        for key, item in sorted(value.items())
                    }
                return [
                    cls._safe_dlq_value(
                        item,
                        depth=depth + 1,
                        ancestors=ancestors,
                        node_budget=node_budget,
                        changes=changes,
                        path=path + "/" + str(index),
                    )
                    for index, item in enumerate(value)
                ]
            finally:
                ancestors.remove(marker)
        return replacement("OPAQUE_TYPE", f"<{type(value).__name__}>")

    @classmethod
    def _safe_dlq_envelope(cls, raw: Mapping[str, object]) -> dict[str, object]:
        if "_radar_dlq_sanitization" in raw:
            return cls._dlq_root_fallback("RESERVED_KEY")
        if len(raw) > cls._MAX_DLQ_WIDTH:
            return cls._dlq_root_fallback("WIDTH_LIMIT")
        if any(type(key) is not str for key in raw):
            return cls._dlq_root_fallback("INVALID_MAP_KEYS")
        if any(len(key) > cls._MAX_DLQ_STRING_CHARS for key in raw):
            return cls._dlq_root_fallback("KEY_LIMIT")
        node_budget = [cls._MAX_DLQ_NODES]
        changes = _DlqChanges()
        sanitized = {
            key: cls._safe_dlq_value(
                value,
                ancestors={id(raw)},
                node_budget=node_budget,
                changes=changes,
                path="/" + cls._dlq_pointer_segment(key),
            )
            for key, value in sorted(raw.items())
        }
        if changes.entries or changes.omitted:
            sanitized["_radar_dlq_sanitization"] = changes.sidecar()
        try:
            serialized = json.dumps(
                sanitized, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            return cls._dlq_root_fallback("NON_JSON_VALUE")
        if len(serialized) > cls._MAX_DLQ_JSON_BYTES:
            return cls._dlq_root_fallback("BYTE_LIMIT")
        return sanitized

    def admit(
        self,
        raw: Mapping[str, object],
        *,
        now_ms: int,
        source_locator: str | None = None,
    ) -> RuntimeReceipt:
        """Classify and admit one raw message into the runtime."""
        result = RadarEnvelope.from_mapping(raw)
        message_id = self._raw_message_id(raw)

        if result.classification is EnvelopeClassification.DLQ:
            item_id = self.dlq.record(
                message_id=message_id,
                failure_stage="ADMISSION",
                failure_code="UNKNOWN_TAXONOMY",
                envelope=self._safe_dlq_envelope(raw),
                diagnostic=",".join(result.errors) or "unknown taxonomy",
                reason_category="TAXONOMY",
                source_locator=source_locator,
            )
            self.telemetry.increment("dlq")
            return RuntimeReceipt(
                state="DLQ",
                message_id=message_id,
                dlq_item_id=item_id,
                errors=result.errors,
            )

        if result.classification is EnvelopeClassification.REJECTED or result.envelope is None:
            self.telemetry.increment("dropped")
            return RuntimeReceipt(
                state="REJECTED",
                message_id=message_id,
                errors=result.errors,
            )

        envelope = result.envelope
        message_id = envelope.message_id
        with self._admission_lock:
            dedupe_key = self._dedupe_key(envelope)
            if not self.dedupe.accept(dedupe_key, now_ms):
                self.telemetry.increment("dropped")
                return RuntimeReceipt(state="DROPPED_DUPLICATE", message_id=message_id)

            try:
                evicted = self.queue.push(envelope)
            except OverflowError:
                self.dedupe.rollback_accept(dedupe_key, now_ms)
                self.telemetry.increment("dropped")
                return RuntimeReceipt(state="DROPPED_QUEUE_FULL", message_id=message_id)

            evicted_message_id = None
            if evicted is not None:
                evicted_message_id = evicted.message_id
                self.telemetry.increment("dropped")

            if envelope.priority == 0:
                dispatched = self.dispatch_next(now_ms=now_ms)
                return replace(dispatched, evicted_message_id=evicted_message_id)

            return RuntimeReceipt(
                state="QUEUED",
                message_id=message_id,
                evicted_message_id=evicted_message_id,
            )

    def dispatch_next(self, *, now_ms: int) -> RuntimeReceipt:
        """Route the highest-priority queued message at an explicit lease time."""
        if type(now_ms) is not int or now_ms < 0:
            raise ValueError("INVALID_ROUTE_TIME")
        with self._admission_lock:
            envelope = self.queue.pop()
            decision = self.router.route(envelope, now_ms=now_ms)
            if not decision.recipients:
                self.telemetry.increment("dropped")
                return RuntimeReceipt(
                    state="DROPPED_NO_ROUTE",
                    message_id=envelope.message_id,
                )

            self.telemetry.increment("routed")
            return RuntimeReceipt(
                state="DISPATCHED",
                message_id=envelope.message_id,
                recipients=decision.recipients,
            )


__all__ = ["RadarRuntime", "RuntimeReceipt"]
