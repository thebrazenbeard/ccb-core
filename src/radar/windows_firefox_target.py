"""Windows/Firefox target adapter for the workstation relay.

The high-level adapter is testable without Windows. A concrete UI Automation
driver is loaded separately so discovery logic can be qualified deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
import time
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
    raw = value.strip()
    if "://" not in raw:
        lowered = raw.lower()
        if any(
            lowered == host or lowered.startswith(host + "/")
            for host in _ALLOWED_CHATGPT_HOSTS
        ):
            raw = "https://" + raw
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() != "https" or host not in _ALLOWED_CHATGPT_HOSTS:
        raise FirefoxTargetError("UNSUPPORTED_CHATGPT_ADDRESS")
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/"


def _is_windows() -> bool:
    return sys.platform == "win32"


class UiautomationFirefoxDriver:
    """Concrete Windows UI Automation driver for Firefox.

    The UIA module is imported lazily so CCB Base remains importable on
    non-Windows hosts. An injected module is accepted for deterministic tests.
    """

    def __init__(
        self,
        *,
        auto_module=None,
        settle_seconds: float = 0.15,
        ack_poll_seconds: float = 0.25,
        max_depth: int = 18,
        composer_names: tuple[str, ...] = (
            "Message ChatGPT",
            "Message GPT",
        ),
    ) -> None:
        if auto_module is None:
            if not _is_windows():
                raise FirefoxTargetError("WINDOWS_REQUIRED")
            try:
                import uiautomation as auto_module
            except ImportError as exc:
                raise FirefoxTargetError(
                    "UIAUTOMATION_DEPENDENCY_REQUIRED"
                ) from exc
        if settle_seconds < 0 or ack_poll_seconds <= 0 or max_depth < 1:
            raise FirefoxTargetError("INVALID_UIA_DRIVER_CONFIGURATION")
        self.auto = auto_module
        self.settle_seconds = settle_seconds
        self.ack_poll_seconds = ack_poll_seconds
        self.max_depth = max_depth
        self.composer_names = tuple(
            name.casefold() for name in composer_names if name.strip()
        )
        self._tabs: dict[tuple[int, str], object] = {}

    def _window(self, window_handle: int):
        control = self.auto.ControlFromHandle(window_handle)
        if control is None:
            raise FirefoxTargetError("FIREFOX_WINDOW_NOT_FOUND")
        return control

    def _walk(
        self,
        window_handle: int,
        *,
        max_depth: int | None = None,
    ):
        window = self._window(window_handle)
        depth = self.max_depth if max_depth is None else max_depth
        return self.auto.WalkControl(
            window,
            includeTop=False,
            maxDepth=depth,
        )

    @staticmethod
    def _runtime_token(control) -> str:
        runtime_id = control.GetRuntimeId()
        if not runtime_id:
            raise FirefoxTargetError("TAB_RUNTIME_ID_UNAVAILABLE")
        return "uia:" + ",".join(str(int(value)) for value in runtime_id)

    @staticmethod
    def _value(control) -> str | None:
        try:
            pattern = control.GetValuePattern()
        except Exception:
            pattern = None
        if pattern is not None:
            try:
                value = pattern.Value
            except Exception:
                value = None
            if isinstance(value, str) and value:
                return value
        try:
            legacy = control.GetLegacyIAccessiblePattern()
        except Exception:
            legacy = None
        if legacy is not None:
            try:
                value = legacy.Value
            except Exception:
                value = None
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _set_value(control, text: str) -> bool:
        try:
            pattern = control.GetValuePattern()
        except Exception:
            pattern = None
        if pattern is not None:
            try:
                if not pattern.IsReadOnly:
                    return bool(pattern.SetValue(text, waitTime=0))
            except Exception:
                pass
        try:
            legacy = control.GetLegacyIAccessiblePattern()
        except Exception:
            legacy = None
        if legacy is not None:
            try:
                return bool(legacy.SetValue(text, waitTime=0))
            except Exception:
                return False
        return False

    def enumerate_firefox_windows(self) -> tuple[int, ...]:
        root = self.auto.GetRootControl()
        handles: list[int] = []
        for control in root.GetChildren():
            try:
                class_name = control.ClassName
                handle = int(control.NativeWindowHandle)
            except Exception:
                continue
            if class_name == "MozillaWindowClass" and handle > 0:
                handles.append(handle)
        return tuple(dict.fromkeys(handles))

    def enumerate_tabs(self, window_handle: int) -> tuple[FirefoxTabRef, ...]:
        for key in tuple(self._tabs):
            if key[0] == window_handle:
                del self._tabs[key]
        tabs: list[FirefoxTabRef] = []
        for control, _depth in self._walk(
            window_handle,
            max_depth=min(6, self.max_depth),
        ):
            try:
                if control.ControlTypeName != "TabItemControl":
                    continue
                name = str(control.Name or "").strip()
                if not name:
                    continue
                token = self._runtime_token(control)
            except Exception:
                continue
            self._tabs[(window_handle, token)] = control
            tabs.append(FirefoxTabRef(window_handle, token, name))
        return tuple(tabs)

    def _tab_control(self, window_handle: int, target_token: str):
        control = self._tabs.get((window_handle, target_token))
        if control is not None:
            return control
        self.enumerate_tabs(window_handle)
        control = self._tabs.get((window_handle, target_token))
        if control is None:
            raise FirefoxTargetError("TARGET_TAB_NOT_FOUND")
        return control

    def select_tab(self, window_handle: int, target_token: str) -> None:
        control = self._tab_control(window_handle, target_token)
        selected = False
        try:
            pattern = control.GetSelectionItemPattern()
        except Exception:
            pattern = None
        if pattern is not None:
            try:
                selected = bool(pattern.Select(waitTime=0))
            except Exception:
                selected = False
        if not selected:
            try:
                control.Click(waitTime=0)
                selected = True
            except Exception as exc:
                raise FirefoxTargetError("TARGET_TAB_SELECT_FAILED") from exc
        if not selected:
            raise FirefoxTargetError("TARGET_TAB_SELECT_FAILED")
        if self.settle_seconds:
            time.sleep(self.settle_seconds)

    def read_address_value(self, window_handle: int) -> str:
        window = self._window(window_handle)
        try:
            exact = self.auto.ComboBoxControl(
                searchFromControl=window,
                searchDepth=self.max_depth,
                AutomationId="urlbar-input",
            )
            if exact.Exists(0.5, 0.05):
                value = self._value(exact)
                if value is not None:
                    normalize_chatgpt_path(value)
                    return value
        except Exception:
            pass

        urlbar_matches: list[str] = []
        fallback_matches: list[str] = []
        for control, _depth in self._walk(window_handle):
            try:
                if control.ControlTypeName not in {
                    "EditControl",
                    "ComboBoxControl",
                }:
                    continue
                automation_id = str(
                    getattr(control, "AutomationId", "") or ""
                ).strip()
            except Exception:
                continue
            value = self._value(control)
            if value is None:
                continue
            try:
                normalize_chatgpt_path(value)
            except FirefoxTargetError:
                continue
            if automation_id == "urlbar-input":
                urlbar_matches.append(value)
            else:
                fallback_matches.append(value)

        candidates = urlbar_matches if urlbar_matches else fallback_matches
        unique = tuple(dict.fromkeys(candidates))
        if not unique:
            raise FirefoxTargetError("CHATGPT_ADDRESS_NOT_FOUND")
        if len(unique) != 1:
            raise FirefoxTargetError("CHATGPT_ADDRESS_AMBIGUOUS")
        return unique[0]

    def read_visible_identity(
        self,
        window_handle: int,
        target_token: str,
    ) -> str:
        control = self._tab_control(window_handle, target_token)
        name = str(getattr(control, "Name", "") or "").strip()
        if not name:
            raise FirefoxTargetError("VISIBLE_IDENTITY_UNAVAILABLE")
        return name

    def _composer(self, window_handle: int):
        matches = []
        for control, _depth in self._walk(window_handle):
            try:
                name = str(control.Name or "").strip().casefold()
            except Exception:
                continue
            if name in self.composer_names:
                matches.append(control)
        if not matches:
            raise FirefoxTargetError("COMPOSER_NOT_FOUND")
        if len(matches) != 1:
            raise FirefoxTargetError("COMPOSER_AMBIGUOUS")
        return matches[0]

    def write_composer(
        self,
        window_handle: int,
        target_token: str,
        text: str,
    ) -> None:
        self._tab_control(window_handle, target_token)
        composer = self._composer(window_handle)
        if not self._set_value(composer, text):
            raise FirefoxTargetError("COMPOSER_WRITE_FAILED")

    def submit_composer(self, window_handle: int, target_token: str) -> None:
        self._tab_control(window_handle, target_token)
        composer = self._composer(window_handle)
        try:
            composer.SendKeys("{Enter}", interval=0, waitTime=0)
        except Exception as exc:
            raise FirefoxTargetError("SUBMIT_NOT_ESTABLISHED") from exc

    def _accessible_texts(self, window_handle: int) -> tuple[str, ...]:
        texts: list[str] = []
        for control, _depth in self._walk(window_handle):
            try:
                name = str(control.Name or "").strip()
            except Exception:
                name = ""
            if name:
                texts.append(name)
            value = self._value(control)
            if value:
                texts.append(value)
        return tuple(dict.fromkeys(texts))

    def read_rendered_message(
        self,
        window_handle: int,
        target_token: str,
        message_id: str,
    ) -> str | None:
        self.select_tab(window_handle, target_token)
        for text in self._accessible_texts(window_handle):
            if (
                "BT2_WORKSTATION_RELAY_V1" in text
                and f"message_id: {message_id}" in text
            ):
                return text
        return None

    def wait_for_ack(
        self,
        window_handle: int,
        target_token: str,
        message_id: str,
        timeout_seconds: float,
    ) -> str | None:
        if timeout_seconds <= 0:
            raise FirefoxTargetError("INVALID_ACK_TIMEOUT")
        deadline = time.monotonic() + timeout_seconds
        while True:
            self.select_tab(window_handle, target_token)
            for text in self._accessible_texts(window_handle):
                if (
                    "BT2_WORKSTATION_RELAY_ACK_V1" in text
                    and f"message_id: {message_id}" in text
                ):
                    return text
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(self.ack_poll_seconds, remaining))


def load_target_descriptor(
    path: str | Path,
    recipient: str,
) -> TargetDescriptor:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FirefoxTargetError("TARGET_CONFIG_UNREADABLE") from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise FirefoxTargetError("TARGET_CONFIG_VERSION_UNSUPPORTED")
    targets = raw.get("targets")
    if not isinstance(targets, list):
        raise FirefoxTargetError("TARGET_CONFIG_INVALID")

    matches = []
    for item in targets:
        if not isinstance(item, dict):
            raise FirefoxTargetError("TARGET_CONFIG_INVALID")
        if item.get("recipient") != recipient:
            continue
        conversation_url = item.get("conversation_url")
        visible_identity = item.get("visible_identity")
        if not isinstance(conversation_url, str):
            raise FirefoxTargetError("TARGET_CONFIG_INVALID")
        if not isinstance(visible_identity, str) or not visible_identity.strip():
            raise FirefoxTargetError("TARGET_CONFIG_INVALID")
        matches.append(
            TargetDescriptor(
                recipient=recipient,
                normalized_url_path=normalize_chatgpt_path(conversation_url),
                visible_identity=visible_identity.strip(),
            )
        )

    if not matches:
        raise FirefoxTargetError("TARGET_CONFIG_NOT_FOUND")
    if len(matches) != 1:
        raise FirefoxTargetError("TARGET_CONFIG_AMBIGUOUS")
    return matches[0]


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
    "UiautomationFirefoxDriver",
    "WindowsFirefoxTarget",
    "load_target_descriptor",
    "normalize_chatgpt_path",
]
