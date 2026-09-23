"""Runtime scheduler for Radar's durable 60-second telemetry heartbeat."""

from __future__ import annotations

from collections.abc import Callable
import logging
import time

from .telemetry import TelemetryStore


logger = logging.getLogger(__name__)

HeartbeatMessage = dict[str, object]
HeartbeatSink = Callable[[HeartbeatMessage], None]
EvidenceSource = str | None | Callable[[], str | None]


class HeartbeatSinkError(RuntimeError):
    """Raised when a durable heartbeat claim cannot be confirmed by its sink."""


def _resolve_evidence(value: EvidenceSource) -> str | None:
    return value() if callable(value) else value


class HeartbeatRuntime:
    """Drive ``TelemetryStore`` heartbeats at the persisted exact due time.

    Clock and sleep functions are injectable so replay and tests remain fully
    deterministic. The scheduler consults durable heartbeat history before every
    sleep, so restarting inside the 60-second window waits only for the remaining
    interval rather than adding a fresh 60-second delay.
    """

    def __init__(
        self,
        store: TelemetryStore,
        *,
        sink: HeartbeatSink,
        clock_ms: Callable[[], int] | None = None,
        sleep: Callable[[float], None] | None = None,
        source_ref: EvidenceSource = None,
        provider_state: EvidenceSource = None,
    ) -> None:
        self.store = store
        self.sink = sink
        self.clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self.sleep = sleep or time.sleep
        self.source_ref = source_ref
        self.provider_state = provider_state

    def tick(self) -> HeartbeatMessage | None:
        """Attempt one due heartbeat and return the sink-confirmed message.

        The store claim is durable before the sink call. A clean sink return marks
        the claim DELIVERED. A sink exception is recorded as UNCONFIRMED rather
        than being blindly retried, because an external effect may already have
        committed before the exception was raised.
        """
        heartbeat = self.store.claim_heartbeat(
            self.clock_ms(),
            source_ref=_resolve_evidence(self.source_ref),
            provider_state=_resolve_evidence(self.provider_state),
        )
        if heartbeat is None:
            return None

        heartbeat_id = heartbeat.get("heartbeat_sequence")
        if type(heartbeat_id) is not int or heartbeat_id <= 0:
            raise RuntimeError("HEARTBEAT_SEQUENCE_MISSING")

        try:
            self.sink(heartbeat)
        except Exception as exc:
            self.store.mark_heartbeat_delivery(heartbeat_id, "UNCONFIRMED")
            raise HeartbeatSinkError("HEARTBEAT_SINK_UNCONFIRMED") from exc

        self.store.mark_heartbeat_delivery(heartbeat_id, "DELIVERED")
        return heartbeat

    def run_forever(self, *, should_stop: Callable[[], bool] | None = None) -> None:
        """Run until ``should_stop`` returns true.

        Sink failures are durably classified and the scheduler continues at the
        next interval. Store/state failures still propagate: those are not safe to
        classify as a transient sink problem.
        """
        stop = should_stop or (lambda: False)
        while not stop():
            try:
                self.tick()
            except HeartbeatSinkError:
                logger.exception(
                    "Radar heartbeat sink attempt is unconfirmed; continuing at next interval"
                )
            if stop():
                return

            delay_ms = self.store.milliseconds_until_heartbeat(self.clock_ms())
            if delay_ms > 0:
                self.sleep(delay_ms / 1_000)


__all__ = [
    "HeartbeatMessage",
    "HeartbeatRuntime",
    "HeartbeatSink",
    "HeartbeatSinkError",
]
