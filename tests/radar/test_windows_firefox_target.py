import importlib.util
import json
from pathlib import Path

import pytest


def test_windows_firefox_target_module_is_shipped():
    assert importlib.util.find_spec("radar.windows_firefox_target") is not None


def test_discovery_selects_tabs_and_requires_both_exact_selectors():
    import radar.windows_firefox_target as firefox
    from radar.workstation_target import TargetDescriptor

    class FakeDriver:
        def __init__(self):
            self.selected = []
            self.current = None
            self.tabs = {
                11: (
                    firefox.FirefoxTabRef(11, "tab-a", "Two — Build Team Two"),
                    firefox.FirefoxTabRef(11, "tab-b", "Two — Build Team Two"),
                ),
                22: (
                    firefox.FirefoxTabRef(22, "tab-c", "Other Chat"),
                ),
            }
            self.values = {
                (11, "tab-a"): ("https://chatgpt.com/c/wrong", "Two — Build Team Two"),
                (11, "tab-b"): ("https://chatgpt.com/c/target-two", "Two — Build Team Two"),
                (22, "tab-c"): ("https://chatgpt.com/c/target-two", "Other Chat"),
            }

        def enumerate_firefox_windows(self):
            return (11, 22)

        def enumerate_tabs(self, window_handle):
            return self.tabs[window_handle]

        def select_tab(self, window_handle, target_token):
            self.selected.append((window_handle, target_token))
            self.current = (window_handle, target_token)

        def read_address_value(self, window_handle):
            return self.values[self.current][0]

        def read_visible_identity(self, window_handle, target_token):
            return self.values[(window_handle, target_token)][1]

    driver = FakeDriver()
    target = firefox.WindowsFirefoxTarget(driver=driver)
    expected = TargetDescriptor(
        recipient="TWO",
        normalized_url_path="/c/target-two",
        visible_identity="Two — Build Team Two",
    )

    matches = target.discover(expected)

    assert [(m.window_handle, m.target_token) for m in matches] == [(11, "tab-b")]
    assert driver.selected == [(11, "tab-a"), (11, "tab-b"), (22, "tab-c")]


def test_snapshot_reselects_exact_tab_and_rereads_both_selectors():
    import radar.windows_firefox_target as firefox

    class FakeDriver:
        def __init__(self):
            self.selected = []

        def select_tab(self, window_handle, target_token):
            self.selected.append((window_handle, target_token))

        def read_address_value(self, window_handle):
            return "https://chatgpt.com/c/target-two?utm_source=ignored"

        def read_visible_identity(self, window_handle, target_token):
            return "Two — Build Team Two"

    driver = FakeDriver()
    target = firefox.WindowsFirefoxTarget(driver=driver)

    observed = target.snapshot(77, "tab-exact")

    assert observed.window_handle == 77
    assert observed.target_token == "tab-exact"
    assert observed.normalized_url_path == "/c/target-two"
    assert observed.visible_identity == "Two — Build Team Two"
    assert driver.selected == [(77, "tab-exact")]


def test_adapter_operations_keep_exact_tab_token_through_visible_text_io():
    import radar.windows_firefox_target as firefox

    class FakeDriver:
        def __init__(self):
            self.calls = []

        def select_tab(self, window_handle, target_token):
            self.calls.append(("select", window_handle, target_token))

        def write_composer(self, window_handle, target_token, text):
            self.calls.append(("write", window_handle, target_token, text))

        def submit_composer(self, window_handle, target_token):
            self.calls.append(("submit", window_handle, target_token))

        def read_rendered_message(self, window_handle, target_token, message_id):
            self.calls.append(("read", window_handle, target_token, message_id))
            return "rendered:" + message_id

        def wait_for_ack(
            self, window_handle, target_token, message_id, timeout_seconds
        ):
            self.calls.append(
                ("ack", window_handle, target_token, message_id, timeout_seconds)
            )
            return "ack-body"

    driver = FakeDriver()
    target = firefox.WindowsFirefoxTarget(driver=driver)

    target.activate(88, "tab-88")
    target.populate(88, "tab-88", "BT2_CANARY: hello")
    target.submit(88, "tab-88")
    rendered = target.read_rendered(88, "tab-88", "msg-88")
    ack = target.wait_for_ack(88, "tab-88", "msg-88", 3.5)

    assert rendered == "rendered:msg-88"
    assert ack == "ack-body"
    assert driver.calls == [
        ("select", 88, "tab-88"),
        ("write", 88, "tab-88", "BT2_CANARY: hello"),
        ("submit", 88, "tab-88"),
        ("read", 88, "tab-88", "msg-88"),
        ("ack", 88, "tab-88", "msg-88", 3.5),
    ]


def test_uiautomation_driver_enumerates_firefox_tabs_and_reads_chatgpt_address():
    import radar.windows_firefox_target as firefox

    class Pattern:
        def __init__(self):
            self.selected = False

        def Select(self, waitTime=0):
            self.selected = True
            return True

    class Value:
        def __init__(self, value):
            self.Value = value
            self.IsReadOnly = False

        def SetValue(self, value, waitTime=0):
            self.Value = value
            return True

    class Control:
        def __init__(
            self, *, class_name="", handle=0, control_type="", name="",
            runtime_id=None, value=None,
        ):
            self.ClassName = class_name
            self.NativeWindowHandle = handle
            self.ControlTypeName = control_type
            self.Name = name
            self._runtime_id = runtime_id or []
            self._value = value
            self.selection = Pattern()

        def GetRuntimeId(self):
            return self._runtime_id

        def GetSelectionItemPattern(self):
            return self.selection

        def GetValuePattern(self):
            return Value(self._value) if self._value is not None else None

    firefox_window = Control(
        class_name="MozillaWindowClass",
        handle=501,
        name="Firefox",
    )
    other_window = Control(class_name="OtherClass", handle=777, name="Other")
    tab = Control(
        control_type="TabItemControl",
        name="Two — Build Team Two",
        runtime_id=[42, 7, 9],
    )
    address = Control(
        control_type="EditControl",
        name="Search with Google or enter address",
        value="https://chatgpt.com/c/target-two?ignored=1",
    )

    class Root:
        def GetChildren(self):
            return [firefox_window, other_window]

    class FakeAuto:
        @staticmethod
        def GetRootControl():
            return Root()

        @staticmethod
        def ControlFromHandle(handle):
            assert handle == 501
            return firefox_window

        @staticmethod
        def WalkControl(control, includeTop=False, maxDepth=0):
            assert control is firefox_window
            return iter(((tab, 2), (address, 3)))

    driver = firefox.UiautomationFirefoxDriver(
        auto_module=FakeAuto,
        settle_seconds=0,
    )

    assert driver.enumerate_firefox_windows() == (501,)
    tabs = driver.enumerate_tabs(501)
    assert tabs == (
        firefox.FirefoxTabRef(501, "uia:42,7,9", "Two — Build Team Two"),
    )

    driver.select_tab(501, "uia:42,7,9")
    assert tab.selection.selected is True
    assert driver.read_address_value(501) == (
        "https://chatgpt.com/c/target-two?ignored=1"
    )
    assert driver.read_visible_identity(501, "uia:42,7,9") == (
        "Two — Build Team Two"
    )


def test_uiautomation_driver_requires_windows_when_backend_not_injected(monkeypatch):
    import radar.windows_firefox_target as firefox

    monkeypatch.setattr(firefox, "_is_windows", lambda: False)
    with pytest.raises(firefox.FirefoxTargetError, match="WINDOWS_REQUIRED"):
        firefox.UiautomationFirefoxDriver()


def test_local_target_config_resolves_one_exact_recipient(tmp_path):
    import radar.windows_firefox_target as firefox

    config = tmp_path / "targets.json"
    config.write_text(
        json.dumps({
            "version": 1,
            "targets": [
                {
                    "recipient": "TWO",
                    "conversation_url": "https://chatgpt.com/c/two?ignored=1",
                    "visible_identity": "Two — Build Team Two",
                },
                {
                    "recipient": "THREE",
                    "conversation_url": "https://chatgpt.com/c/three",
                    "visible_identity": "Three — Build Team Two",
                },
            ],
        }),
        encoding="utf-8",
    )

    descriptor = firefox.load_target_descriptor(config, "TWO")

    assert descriptor.recipient == "TWO"
    assert descriptor.normalized_url_path == "/c/two"
    assert descriptor.visible_identity == "Two — Build Team Two"


def test_local_target_config_rejects_duplicate_recipient(tmp_path):
    import radar.windows_firefox_target as firefox

    config = tmp_path / "targets.json"
    config.write_text(
        json.dumps({
            "version": 1,
            "targets": [
                {
                    "recipient": "TWO",
                    "conversation_url": "https://chatgpt.com/c/one",
                    "visible_identity": "Two — One",
                },
                {
                    "recipient": "TWO",
                    "conversation_url": "https://chatgpt.com/c/two",
                    "visible_identity": "Two — Two",
                },
            ],
        }),
        encoding="utf-8",
    )

    with pytest.raises(
        firefox.FirefoxTargetError,
        match="TARGET_CONFIG_AMBIGUOUS",
    ):
        firefox.load_target_descriptor(config, "TWO")
