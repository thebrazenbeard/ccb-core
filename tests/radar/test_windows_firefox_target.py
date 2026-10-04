import importlib.util


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
