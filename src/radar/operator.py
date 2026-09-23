"""ChatGPT-facing operator projections for Radar."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Protocol, Sequence, Tuple


BOOTSTRAP_ORDER: Tuple[str, ...] = (
    "contract",
    "github",
    "provider",
    "reconcile",
    "exceptions",
    "continue",
)


class OperatorError(ValueError):
    """Malformed operator projection input."""


class Reconciler(Protocol):
    def compare(
        self,
        github_state: Mapping[str, Any],
        provider_state: Mapping[str, Any],
    ) -> Iterable[Any]:
        ...


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OperatorError(code)
    return value


def _bool(value: object, code: str) -> bool:
    if not isinstance(value, bool):
        raise OperatorError(code)
    return value


def _sequence(value: object, code: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise OperatorError(code)
    return value


class RadarOperator:
    """Stateless projections for human and machine Radar operators."""

    @staticmethod
    def status(
        *,
        identity_count: int,
        online_node_count: int,
        chat_session_active: bool,
        **extra: Any,
    ) -> Dict[str, Any]:
        status: Dict[str, Any] = {
            "identity_count": _nonnegative_int(
                identity_count, "INVALID_IDENTITY_COUNT"
            ),
            "online_node_count": _nonnegative_int(
                online_node_count, "INVALID_ONLINE_NODE_COUNT"
            ),
            "chat_session_active": _bool(
                chat_session_active, "INVALID_CHAT_SESSION_ACTIVE"
            ),
        }
        status.update(extra)
        return status

    @staticmethod
    def health(*, findings: Sequence[Any] = ()) -> Dict[str, Any]:
        findings_value = _sequence(findings, "INVALID_FINDINGS")
        findings_list: List[Any] = list(findings_value)
        return {"finding_count": len(findings_list), "findings": findings_list}

    @staticmethod
    def dependencies(
        *, ready: Sequence[str] = (), waiting: Sequence[str] = ()
    ) -> Dict[str, Any]:
        ready_value = _sequence(ready, "INVALID_DEPENDENCIES")
        waiting_value = _sequence(waiting, "INVALID_DEPENDENCIES")
        return {"ready": list(ready_value), "waiting": list(waiting_value)}

    @staticmethod
    def dlq(*, pending: int = 0) -> Dict[str, Any]:
        return {"pending": _nonnegative_int(pending, "INVALID_DLQ_PENDING")}

    @staticmethod
    def audit(*, findings: Sequence[Any] = ()) -> Dict[str, Any]:
        findings_value = _sequence(findings, "INVALID_FINDINGS")
        findings_list: List[Any] = list(findings_value)
        return {"finding_count": len(findings_list), "findings": findings_list}

    @staticmethod
    def reconcile(
        reconciler: Reconciler,
        github_state: Mapping[str, Any],
        provider_state: Mapping[str, Any],
    ) -> Tuple[Any, ...]:
        return tuple(reconciler.compare(github_state, provider_state))


__all__ = ["BOOTSTRAP_ORDER", "OperatorError", "Reconciler", "RadarOperator"]
