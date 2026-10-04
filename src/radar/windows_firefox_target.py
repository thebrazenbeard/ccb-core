"""Windows/Firefox target adapter for the workstation relay.

The high-level adapter is testable without Windows. A concrete UI Automation
driver is loaded separately so discovery logic can be qualified deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from .workstation_target import TargetDescriptor, TargetSnapshot


ADAPTER = "WINDOWS_FIREFOX_UIA_V1"
_ALLOWED_CHATGPT_HOSTS = frozenset({"chatgpt.com", "chat.openai.com"})


class FirefoxTargetError(ValueError):
    pass


@dataclass(frozen=True)
class FirefoxTabRef:
    window_handle: int
    target_token: str
    visible_tab_name: str


class FirefoxUiDriver(Protocol):
    def enumerate_firefox_windows(self) -> tuple[int, ...]: ...
    def enumerate_tabs(self, window_handle: int) -> tuple[FirefoxTabRef, ...]: ...
    def select_tab(self, window_handle: int, target_token: str) -> None: ...
    def read_address_value(self, window_handle: int) -> str: ...
    def read_visible_identity(
        self, window_handle: int, target_token: str
    ) -> str: ...
    def write_composer(
        self, window_handle: int, target_token: str, text: str
    ) -> None: ...
    def submit_composer(self, window_handle: int, target_token: str) -> None: ...
    def read_rendered_message(
        self, window_handle: int, target_token: str, message_id: str
    ) -> str | None: ...
    def wait_for_ack(
        self, window_handle: int, target_token: str,
        message_id: str, timeout_seconds: float
    ) -> str | None: ...


def normalize_chatgpt_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FirefoxTargetError("ADDRESS_VALUE_REQUIRED")
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() != "https" or host not in _ALLOWED_CHATGPT_HOSTS:
        raise FirefoxTargetError("UNSUPPORTED_CHATGPT_ADDRESS")
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/"


class WindowsFirefoxTarget:
    def __init__(self, *, driver: FirefoxUiDriver) -> None:
        self.driver = driver

    def discover(
        self, expected: TargetDescriptor
    ) -> tuple[TargetSnapshot, ...]:
        matches: list[TargetSnapshot] = []
        for window_handle in self.driver.enumerate_firefox_windows():
            for tab in self.driver.enumerate_tabs(window_handle):
                if tab.window_handle != window_handle:
                    continue
                self.driver.select_tab(window_handle, tab.target_token)
                try:
                    normalized_path = normalize_chatgpt_path(
                        self.driver.read_address_value(window_handle)
                    )
                    visible_identity = self.driver.read_visible_identity(
                        window_handle,
                        tab.target_token,
                    )
                except Exception:
                    continue
                snapshot = TargetSnapshot(
                    window_handle=window_handle,
                    target_token=tab.target_token,
                    normalized_url_path=normalized_path,
                    visible_identity=visible_identity,
                )
                if snapshot.matches(expected):
                    matches.append(snapshot)
        return tuple(matches)

    def snapshot(
        self,
        window_handle: int,
        target_token: str | None,
    ) -> TargetSnapshot:
        if target_token is None or not target_token.strip():
            raise FirefoxTargetError("TARGET_TOKEN_REQUIRED")
        self.driver.select_tab(window_handle, target_token)
        normalized_path = normalize_chatgpt_path(
            self.driver.read_address_value(window_handle)
        )
        visible_identity = self.driver.read_visible_identity(
            window_handle,
            target_token,
        )
        return TargetSnapshot(
            window_handle=window_handle,
            target_token=target_token,
            normalized_url_path=normalized_path,
            visible_identity=visible_identity,
        )

    def activate(self, window_handle: int, target_token: str | None) -> None:
        if target_token is None or not target_token.strip():
            raise FirefoxTargetError("TARGET_TOKEN_REQUIRED")
        self.driver.select_tab(window_handle, target_token)

    def populate(
        self,
        window_handle: int,
        target_token: str | None,
        text: str,
    ) -> None:
        if target_token is None or not target_token.strip():
            raise FirefoxTargetError("TARGET_TOKEN_REQUIRED")
        self.driver.write_composer(window_handle, target_token, text)

    def submit(self, window_handle: int, target_token: str | None) -> None:
        if target_token is None or not target_token.strip():
            raise FirefoxTargetError("TARGET_TOKEN_REQUIRED")
        self.driver.submit_composer(window_handle, target_token)

    def read_rendered(
        self,
        window_handle: int,
        target_token: str | None,
        message_id: str,
    ) -> str | None:
        if target_token is None or not target_token.strip():
            raise FirefoxTargetError("TARGET_TOKEN_REQUIRED")
        return self.driver.read_rendered_message(
            window_handle,
            target_token,
            message_id,
        )

    def wait_for_ack(
        self,
        window_handle: int,
        target_token: str | None,
        message_id: str,
        timeout_seconds: float,
    ) -> str | None:
        if target_token is None or not target_token.strip():
            raise FirefoxTargetError("TARGET_TOKEN_REQUIRED")
        return self.driver.wait_for_ack(
            window_handle,
            target_token,
            message_id,
            timeout_seconds,
        )


__all__ = [
    "ADAPTER",
    "FirefoxTabRef",
    "FirefoxTargetError",
    "FirefoxUiDriver",
    "WindowsFirefoxTarget",
    "normalize_chatgpt_path",
]
