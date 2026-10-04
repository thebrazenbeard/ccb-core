import importlib.util


def test_windows_firefox_target_module_is_shipped():
    assert importlib.util.find_spec("radar.windows_firefox_target") is not None
