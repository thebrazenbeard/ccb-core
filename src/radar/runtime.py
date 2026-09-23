"""Canonical in-process Radar admission, queueing, routing, and accounting path."""

from __future__ import annotations

from dataclasses import dataclass, replace
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


class RadarRuntime:
    """Join Radar's admission, queueing, routing, and accounting seams."""

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
    def _safe_dlq_value(cls, value: object) -> object:
        if value is None or isinstance(value, (str, bool, int, float)):
            return value
        if isinstance(value, Mapping):
            return {
                str(key): cls._safe_dlq_value(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        if isinstance(value, (list, tuple)):
            return [cls._safe_dlq_value(item) for item in value]
        return f"<{type(value).__name__}>"

    @classmethod
    def _safe_dlq_envelope(cls, raw: Mapping[str, object]) -> dict[str, object]:
        return {str(key): cls._safe_dlq_value(value) for key, value in raw.items()}

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
