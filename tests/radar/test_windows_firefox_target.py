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


def test_snapshot_reads_both_selectors_without_reselecting():
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
    assert driver.selected == []


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
    active_document = Control(
        control_type="DocumentControl",
        name="Two — Build Team Two",
    )
    active_document.IsOffscreen = False

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
            return iter(((tab, 2), (address, 3), (active_document, 4)))

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


def test_uia_driver_prefers_firefox_urlbar_over_page_edit_decoy():
    import radar.windows_firefox_target as firefox

    class Value:
        def __init__(self, value):
            self.Value = value

    class Control:
        def __init__(self, *, control_type="", automation_id="", value=None):
            self.ControlTypeName = control_type
            self.AutomationId = automation_id
            self._value = value

        def GetValuePattern(self):
            return Value(self._value) if self._value is not None else None

    urlbar = Control(
        control_type="EditControl",
        automation_id="urlbar-input",
        value="https://chatgpt.com/c/right",
    )
    decoy = Control(
        control_type="EditControl",
        automation_id="prompt-textarea",
        value="https://chatgpt.com/c/wrong",
    )

    class FakeAuto:
        @staticmethod
        def ControlFromHandle(handle):
            return object()

        @staticmethod
        def WalkControl(control, includeTop=False, maxDepth=0):
            return iter(((decoy, 4), (urlbar, 2)))

    driver = firefox.UiautomationFirefoxDriver(
        auto_module=FakeAuto,
        settle_seconds=0,
    )
    assert driver.read_address_value(501) == "https://chatgpt.com/c/right"


def test_post_submit_readback_reselects_bound_tab_before_observation():
    import radar.windows_firefox_target as firefox

    class Pattern:
        def __init__(self):
            self.selected = 0

        def Select(self, waitTime=0):
            self.selected += 1
            return True

    class Tab:
        ControlTypeName = "TabItemControl"
        Name = "Two — Build Team Two"

        def __init__(self):
            self.selection = Pattern()

        def GetRuntimeId(self):
            return [9, 8, 7]

        def GetSelectionItemPattern(self):
            return self.selection

    class Document:
        ControlTypeName = "DocumentControl"
        Name = "Two — Build Team Two"
        IsOffscreen = False

        def GetValuePattern(self):
            return None

    window = object()
    tab = Tab()
    document = Document()

    class FakeAuto:
        @staticmethod
        def ControlFromHandle(handle):
            return window

        @staticmethod
        def WalkControl(control, includeTop=False, maxDepth=0):
            if control is window:
                return iter(((tab, 2), (document, 4)))
            if control is document:
                return iter(())
            return iter(())

    driver = firefox.UiautomationFirefoxDriver(
        auto_module=FakeAuto,
        settle_seconds=0,
    )
    driver.enumerate_tabs(501)
    driver.read_rendered_message(501, "uia:9,8,7", "msg-1")
    assert tab.selection.selected == 1


def test_uia_driver_accepts_live_firefox_urlbar_combobox_control():
    import radar.windows_firefox_target as firefox

    class Value:
        def __init__(self, value):
            self.Value = value

    class Control:
        ControlTypeName = "ComboBoxControl"
        AutomationId = "urlbar-input"

        def GetValuePattern(self):
            return Value("https://chatgpt.com/c/live-combobox")

    class FakeAuto:
        @staticmethod
        def ControlFromHandle(handle):
            return object()

        @staticmethod
        def WalkControl(control, includeTop=False, maxDepth=0):
            return iter(((Control(), 2),))

    driver = firefox.UiautomationFirefoxDriver(
        auto_module=FakeAuto,
        settle_seconds=0,
    )
    assert driver.read_address_value(501) == "https://chatgpt.com/c/live-combobox"


def test_normalize_chatgpt_path_accepts_live_schemeless_address_value():
    import radar.windows_firefox_target as firefox

    assert (
        firefox.normalize_chatgpt_path(
            "chatgpt.com/g/project/c/live-conversation"
        )
        == "/g/project/c/live-conversation"
    )


def test_uia_driver_uses_exact_urlbar_lookup_before_tree_walk():
    import radar.windows_firefox_target as firefox

    class Value:
        Value = "chatgpt.com/c/fast"

    class Urlbar:
        ControlTypeName = "ComboBoxControl"
        AutomationId = "urlbar-input"

        def Exists(self, maxSearchSeconds, searchIntervalSeconds):
            return True

        def GetValuePattern(self):
            return Value()

    class FakeAuto:
        @staticmethod
        def ControlFromHandle(handle):
            return object()

        @staticmethod
        def ComboBoxControl(**kwargs):
            assert kwargs["AutomationId"] == "urlbar-input"
            return Urlbar()

        @staticmethod
        def WalkControl(*args, **kwargs):
            raise AssertionError("full tree walk should not run when exact urlbar exists")

    driver = firefox.UiautomationFirefoxDriver(
        auto_module=FakeAuto,
        settle_seconds=0,
    )
    assert driver.read_address_value(501) == "chatgpt.com/c/fast"


def test_uia_tab_discovery_bounds_browser_chrome_walk_depth():
    import radar.windows_firefox_target as firefox

    observed = []

    class FakeAuto:
        @staticmethod
        def ControlFromHandle(handle):
            return object()

        @staticmethod
        def WalkControl(control, includeTop=False, maxDepth=0):
            observed.append(maxDepth)
            return iter(())

    driver = firefox.UiautomationFirefoxDriver(
        auto_module=FakeAuto,
        settle_seconds=0,
        max_depth=18,
    )
    assert driver.enumerate_tabs(501) == ()
    assert observed == [6]


def test_uia_driver_uses_injected_native_window_enumerator_without_uia_root():
    import radar.windows_firefox_target as firefox

    class FakeAuto:
        @staticmethod
        def GetRootControl():
            raise AssertionError("desktop UIA root must not be consulted")

    driver = firefox.UiautomationFirefoxDriver(
        auto_module=FakeAuto,
        window_enumerator=lambda: (501, 502, 501),
        settle_seconds=0,
    )
    assert driver.enumerate_firefox_windows() == (501, 502)


def test_snapshot_observes_current_state_without_reselecting_target():
    import radar.windows_firefox_target as firefox

    class FakeDriver:
        def __init__(self):
            self.selected = []

        def select_tab(self, window_handle, target_token):
            self.selected.append((window_handle, target_token))
            raise AssertionError("presubmit snapshot must not reselect target")

        def read_address_value(self, window_handle):
            return "https://chatgpt.com/c/current-visible-tab"

        def read_visible_identity(self, window_handle, target_token):
            return "Current visible chat"

    driver = FakeDriver()
    target = firefox.WindowsFirefoxTarget(driver=driver)

    observed = target.snapshot(77, "tab-bound")

    assert driver.selected == []
    assert observed.normalized_url_path == "/c/current-visible-tab"
    assert observed.visible_identity == "Current visible chat"


def test_uia_composer_is_scoped_to_exact_active_document_and_accepts_live_name():
    import radar.windows_firefox_target as firefox

    class Selection:
        IsSelected = True

        def Select(self, waitTime=0):
            return True

    class Value:
        def __init__(self):
            self.Value = ""
            self.IsReadOnly = False

        def SetValue(self, value, waitTime=0):
            self.Value = value
            return True

    class Control:
        def __init__(
            self,
            *,
            control_type="",
            name="",
            class_name="",
            offscreen=False,
            runtime_id=None,
            value_pattern=None,
        ):
            self.ControlTypeName = control_type
            self.Name = name
            self.ClassName = class_name
            self.AutomationId = ""
            self.IsOffscreen = offscreen
            self._runtime_id = runtime_id or []
            self._value_pattern = value_pattern
            self.selection = Selection()

        def GetRuntimeId(self):
            return self._runtime_id

        def GetSelectionItemPattern(self):
            return self.selection

        def GetValuePattern(self):
            return self._value_pattern

    window = Control(name="Firefox")
    tab = Control(
        control_type="TabItemControl",
        name="Model Training Lane C",
        runtime_id=[42, 1, 9],
    )
    active_doc = Control(
        control_type="DocumentControl",
        name="Model Training Lane C",
        offscreen=False,
    )
    wrong_doc = Control(
        control_type="DocumentControl",
        name="Other Chat",
        offscreen=True,
    )
    composer_root = Control(
        control_type="GroupControl",
        class_name="ComposerLayoutRoot-X",
    )
    real_value = Value()
    real_composer = Control(
        control_type="EditControl",
        name="Ask ChatGPT",
        class_name="ProseMirror ProseMirror-focused",
        value_pattern=real_value,
    )
    decoy_value = Value()
    decoy_composer = Control(
        control_type="EditControl",
        name="Message ChatGPT",
        class_name="ProseMirror",
        value_pattern=decoy_value,
    )

    class FakeAuto:
        @staticmethod
        def ControlFromHandle(handle):
            return window

        @staticmethod
        def WalkControl(control, includeTop=False, maxDepth=0):
            if control is window:
                return iter(((tab, 2), (active_doc, 4), (wrong_doc, 4)))
            if control is active_doc:
                return iter(((composer_root, 8),))
            if control is composer_root:
                return iter(((real_composer, 5),))
            if control is wrong_doc:
                return iter(((decoy_composer, 5),))
            return iter(())

    driver = firefox.UiautomationFirefoxDriver(
        auto_module=FakeAuto,
        window_enumerator=lambda: (501,),
        settle_seconds=0,
    )
    driver.enumerate_tabs(501)
    driver.write_composer(501, "uia:42,1,9", "BT2_CANARY: marker=live")

    assert real_value.Value == "BT2_CANARY: marker=live"
    assert decoy_value.Value == ""


def test_rendered_readback_cannot_match_message_from_other_tab_document():
    import radar.windows_firefox_target as firefox

    class Selection:
        IsSelected = True

        def Select(self, waitTime=0):
            return True

    class Control:
        def __init__(
            self,
            *,
            control_type="",
            name="",
            offscreen=False,
            runtime_id=None,
        ):
            self.ControlTypeName = control_type
            self.Name = name
            self.ClassName = ""
            self.AutomationId = ""
            self.IsOffscreen = offscreen
            self._runtime_id = runtime_id or []
            self.selection = Selection()

        def GetRuntimeId(self):
            return self._runtime_id

        def GetSelectionItemPattern(self):
            return self.selection

        def GetValuePattern(self):
            return None

    window = Control(name="Firefox")
    tab = Control(
        control_type="TabItemControl",
        name="Target Chat",
        runtime_id=[42, 2, 9],
    )
    target_doc = Control(
        control_type="DocumentControl",
        name="Target Chat",
        offscreen=False,
    )
    other_doc = Control(
        control_type="DocumentControl",
        name="Other Chat",
        offscreen=True,
    )
    target_text = Control(
        control_type="TextControl",
        name="ordinary target text",
    )
    decoy_text = Control(
        control_type="TextControl",
        name=(
            "BT2_WORKSTATION_RELAY_V1\n"
            "message_id: msg-decoy\n"
            "nonce: abc"
        ),
    )

    class FakeAuto:
        @staticmethod
        def ControlFromHandle(handle):
            return window

        @staticmethod
        def WalkControl(control, includeTop=False, maxDepth=0):
            if control is window:
                return iter((
                    (tab, 2),
                    (target_doc, 4),
                    (other_doc, 4),
                    (decoy_text, 16),
                ))
            if control is target_doc:
                return iter(((target_text, 2),))
            if control is other_doc:
                return iter(((decoy_text, 2),))
            return iter(())

    driver = firefox.UiautomationFirefoxDriver(
        auto_module=FakeAuto,
        window_enumerator=lambda: (501,),
        settle_seconds=0,
    )
    driver.enumerate_tabs(501)

    assert driver.read_rendered_message(
        501,
        "uia:42,2,9",
        "msg-decoy",
    ) is None


def test_uia_set_value_falls_back_to_legacy_when_value_pattern_returns_false():
    import radar.windows_firefox_target as firefox

    calls = []

    class ValuePattern:
        IsReadOnly = False

        def SetValue(self, text, waitTime=0):
            calls.append(("value", text))
            return False

    class LegacyPattern:
        def SetValue(self, text, waitTime=0):
            calls.append(("legacy", text))
            return True

    class Control:
        def GetValuePattern(self):
            return ValuePattern()

        def GetLegacyIAccessiblePattern(self):
            return LegacyPattern()

    assert firefox.UiautomationFirefoxDriver._set_value(
        Control(),
        "BT2_CANARY: marker=value-fallback",
    ) is True
    assert calls == [
        ("value", "BT2_CANARY: marker=value-fallback"),
        ("legacy", "BT2_CANARY: marker=value-fallback"),
    ]
