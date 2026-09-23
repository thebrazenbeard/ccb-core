"""Branch-bound admission wrapper for supported Bus appends.

The lower-level SafeLaneAppender derives the physical writer ref from trusted
topology. This wrapper additionally authenticates the message envelope's
declared ``writer_branch`` against that topology on every control snapshot read.
"""

from __future__ import annotations


class BranchBindingError(RuntimeError):
    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        suffix = f": {detail}" if detail else ""
        super().__init__(f"{code}{suffix}")


def _exact_text(message_utf8: str | bytes) -> str:
    if isinstance(message_utf8, str):
        return message_utf8
    if isinstance(message_utf8, bytes):
        try:
            return message_utf8.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BranchBindingError("MESSAGE_NOT_UTF8") from exc
    raise BranchBindingError("MESSAGE_NOT_UTF8")


class _BranchCheckingControlSource:
    def __init__(self, source, *, identity: str, declared_branch: str) -> None:
        self._source = source
        self._identity = identity
        self._declared_branch = declared_branch

    def read(self):
        snapshot = self._source.read()
        lanes = getattr(snapshot, "lanes", None)
        binding = lanes.get(self._identity) if lanes is not None else None
        if binding is not None:
            branch = getattr(binding, "branch", None)
            if branch != self._declared_branch:
                raise BranchBindingError(
                    "WRITER_BRANCH_MISMATCH",
                    f"declared={self._declared_branch!r}, topology={branch!r}",
                )
        return snapshot


def _unwrap_branch_binding_cause(exc: Exception) -> None:
    """Restore the envelope/topology mismatch code only during preflight.

    SafeLaneAppender intentionally assigns distinct taxonomy to later control
    reads. A before-ref failure must remain TOPOLOGY_CHANGED_DURING_APPEND, and a
    post-write failure must retain its receipt/effect state. Only the initial
    TOPOLOGY_UNAVAILABLE normalization is safely refined back to this wrapper's
    WRITER_BRANCH_MISMATCH code.
    """

    cause = exc.__cause__
    if not isinstance(cause, BranchBindingError):
        return

    if (
        getattr(exc, "code", None) == "TOPOLOGY_UNAVAILABLE"
        and getattr(exc, "effect_state", None) == "NONE_ESTABLISHED"
    ):
        raise cause from exc


class BranchBoundSafeLaneAppender:
    """Supported append surface that binds envelope branch to trusted topology."""

    def __init__(
        self,
        *,
        control_source,
        history_source,
        git_backend,
        delegate_factory=None,
        header_parser=None,
    ) -> None:
        if delegate_factory is None or header_parser is None:
            from .safe_lane_append import SafeLaneAppender, _metadata_headers

            if delegate_factory is None:
                delegate_factory = SafeLaneAppender
            if header_parser is None:
                header_parser = _metadata_headers

        self._control_source = control_source
        self._history_source = history_source
        self._git_backend = git_backend
        self._delegate_factory = delegate_factory
        self._header_parser = header_parser

    def append_bus_message_v1(
        self,
        identity: str,
        expected_lane_head: str,
        message_utf8: str | bytes,
    ):
        headers = self._header_parser(_exact_text(message_utf8))
        declared_branch = headers.get("writer_branch")
        if not declared_branch:
            raise BranchBindingError("WRITER_BRANCH_MISSING")

        checked_control = _BranchCheckingControlSource(
            self._control_source,
            identity=identity,
            declared_branch=declared_branch,
        )
        delegate = self._delegate_factory(
            control_source=checked_control,
            history_source=self._history_source,
            git_backend=self._git_backend,
        )
        try:
            return delegate.append_bus_message_v1(
                identity,
                expected_lane_head,
                message_utf8,
            )
        except BranchBindingError:
            raise
        except Exception as exc:
            _unwrap_branch_binding_cause(exc)
            raise


__all__ = ["BranchBindingError", "BranchBoundSafeLaneAppender"]
