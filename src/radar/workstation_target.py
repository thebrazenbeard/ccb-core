"""Transport-neutral target identity contract for workstation relay."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Protocol


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class TargetError(ValueError):
    pass


@dataclass(frozen=True)
class TargetDescriptor:
    recipient: str
    normalized_url_path: str
    visible_identity: str

    def __post_init__(self) -> None:
        if _ID_RE.fullmatch(self.recipient) is None:
            raise TargetError("INVALID_TARGET_RECIPIENT")
        if not self.normalized_url_path.startswith("/"):
            raise TargetError("INVALID_TARGET_URL_PATH")
        if not self.visible_identity.strip():
            raise TargetError("INVALID_TARGET_VISIBLE_IDENTITY")
        if self.target_token is not None and not self.target_token.strip():
            raise TargetError("INVALID_TARGET_TOKEN")


@dataclass(frozen=True)
class TargetSnapshot:
    window_handle: int
    normalized_url_path: str
    visible_identity: str
    target_token: str | None = None

    def __post_init__(self) -> None:
        if type(self.window_handle) is not int or self.window_handle <= 0:
            raise TargetError("INVALID_TARGET_WINDOW_HANDLE")
        if not self.normalized_url_path.startswith("/"):
            raise TargetError("INVALID_TARGET_URL_PATH")
        if not self.visible_identity.strip():
            raise TargetError("INVALID_TARGET_VISIBLE_IDENTITY")

    @property
    def selector_digest(self) -> str:
        canonical = json.dumps(
            {
                "normalized_url_path": self.normalized_url_path,
                "visible_identity": self.visible_identity,
                "target_token": self.target_token,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def matches(self, descriptor: TargetDescriptor) -> bool:
        return (
            self.normalized_url_path == descriptor.normalized_url_path
            and self.visible_identity == descriptor.visible_identity
        )


class WorkstationTarget(Protocol):
    def discover(self, expected: TargetDescriptor) -> tuple[TargetSnapshot, ...]: ...
    def activate(self, window_handle: int, target_token: str | None) -> None: ...
    def snapshot(
        self, window_handle: int, target_token: str | None
    ) -> TargetSnapshot: ...
    def populate(
        self, window_handle: int, target_token: str | None, text: str
    ) -> None: ...
    def submit(self, window_handle: int, target_token: str | None) -> None: ...
    def read_rendered(
        self, window_handle: int, target_token: str | None, message_id: str
    ) -> str | None: ...
    def wait_for_ack(
        self, window_handle: int, target_token: str | None,
        message_id: str, timeout_seconds: float
    ) -> str | None: ...


__all__ = [
    "TargetDescriptor",
    "TargetError",
    "TargetSnapshot",
    "WorkstationTarget",
]
