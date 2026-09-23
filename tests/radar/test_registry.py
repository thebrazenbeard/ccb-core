import importlib
import threading

import pytest


def _module():
    try:
        return importlib.import_module("radar.registry")
    except Exception as exc:
        pytest.fail(f"Radar registry implementation missing: {exc}")


def test_identity_survives_node_offline_state():
    m = _module()
    r = m.IdentityRegistry()
    r.register_identity("yin", display_name="Yin")
    r.register_node("node-yin-1", identity_id="yin", status=m.NodeStatus.ONLINE)
    r.set_node_status("node-yin-1", m.NodeStatus.OFFLINE)
    assert r.identity("yin").display_name == "Yin"
    assert r.node("node-yin-1").status is m.NodeStatus.OFFLINE


def test_alias_collision_fails_deterministically():
    m = _module()
    r = m.IdentityRegistry()
    r.register_identity("beta", display_name="beta", aliases=("beta-legacy",))
    with pytest.raises(m.RegistryError, match="ALIAS_COLLISION"):
        r.register_identity("someone_else", display_name="Else", aliases=("beta-legacy",))


def test_alias_collision_is_rechecked_under_lock_for_concurrent_registration():
    m = _module()
    registry = m.IdentityRegistry()
    barrier = threading.Barrier(2)
    results: list[tuple[str, object]] = []
    results_lock = threading.Lock()

    class CoordinatedAliases:
        def __iter__(self):
            yield "shared-alias"
            # The next iterator step happens only after the caller has checked
            # the yielded alias against its pre-lock dictionaries.
            barrier.wait(timeout=5)

    def register(identity_id: str) -> None:
        try:
            value = registry.register_identity(
                identity_id,
                display_name=identity_id,
                aliases=CoordinatedAliases(),
            )
            outcome: tuple[str, object] = ("ok", value)
        except Exception as exc:  # captured so both threads can join cleanly
            outcome = ("error", exc)
        with results_lock:
            results.append(outcome)

    threads = [
        threading.Thread(target=register, args=("one",)),
        threading.Thread(target=register, args=("two",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    successes = [value for kind, value in results if kind == "ok"]
    failures = [value for kind, value in results if kind == "error"]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], m.RegistryError)
    assert str(failures[0]) == "ALIAS_COLLISION"
    assert registry.resolve_identity_id("shared-alias") == successes[0].identity_id


def test_subscription_priority_rejects_booleans_instead_of_int_coercion():
    m = _module()
    r = m.IdentityRegistry()
    r.register_identity("one", display_name="One")
    with pytest.raises(m.RegistryError, match="INVALID_PRIORITY"):
        r.subscribe("one", domain="system", min_priority=True)
    with pytest.raises(m.RegistryError, match="INVALID_PRIORITY"):
        r.subscriptions_for_domain("system", False)


def test_endpoint_is_separate_from_identity_and_node():
    m = _module()
    r = m.IdentityRegistry()
    r.register_identity("radar", display_name="Radar")
    r.register_endpoint("github-radar", identity_id="radar", transport="github", address="example/ccb-core")
    assert r.endpoint("github-radar").identity_id == "radar"
    assert r.identity("radar").identity_id == "radar"
