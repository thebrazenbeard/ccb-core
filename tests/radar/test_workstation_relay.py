import importlib.util


def test_workstation_relay_module_is_shipped():
    assert importlib.util.find_spec("radar.workstation_relay") is not None
