import importlib
import pytest


NOW_MS = 1_500


def _modules():
    try:
        return importlib.import_module("radar.envelope"), importlib.import_module("radar.registry"), importlib.import_module("radar.routing")
    except Exception as exc:
        pytest.fail(f"Radar routing implementation missing: {exc}")


def _env(e, **overrides):
    raw = {"schema_version": 1, "message_id": "m1", "created_at": "2026-09-02T12:00:00Z", "sender": "alpha", "audience": [], "domain": "database", "intent": "event", "priority": 2, "payload": {}}
    raw.update(overrides)
    return e.RadarEnvelope.from_mapping(raw).envelope


def _activate(reg, rmod, identity, *, min_priority=4):
    reg.register_identity(identity, display_name=identity.title())
    node_id = f"{identity}-node"
    reg.register_node(
        node_id,
        identity_id=identity,
        status=rmod.NodeStatus.ONLINE,
        last_heartbeat_ms=1_000,
        lease_expires_ms=2_000,
    )
    reg.subscribe(node_id, domain="database", min_priority=min_priority)


def test_direct_audience_routes_only_to_named_authorized_live_node():
    e, rmod, rt = _modules()
    reg = rmod.IdentityRegistry()
    _activate(reg, rmod, "yin")
    _activate(reg, rmod, "yang")
    decision = rt.Router(reg).route(_env(e, audience=["yin"]), now_ms=NOW_MS)
    assert decision.recipients == ("yin-node",)


def test_subscription_route_respects_domain_min_priority_and_live_registration():
    e, rmod, rt = _modules()
    reg = rmod.IdentityRegistry()
    _activate(reg, rmod, "gamma", min_priority=2)
    assert rt.Router(reg).route(_env(e, priority=1), now_ms=NOW_MS).recipients == ("gamma-node",)
    assert rt.Router(reg).route(_env(e, priority=3), now_ms=NOW_MS).recipients == ()


def test_priority_queue_orders_without_granting_authority():
    e, _, rt = _modules()
    q = rt.PriorityQueue()
    q.push(_env(e, message_id="slow", priority=4))
    q.push(_env(e, message_id="urgent", priority=0))
    first = q.pop()
    assert first.message_id == "urgent"
    assert first.authority_ref is None


def test_priority_zero_eviction_returns_dropped_envelope_for_telemetry():
    e, _, rt = _modules()
    q = rt.PriorityQueue(max_size=2)
    q.push(_env(e, message_id="old-background", priority=4))
    q.push(_env(e, message_id="new-background", priority=4))
    evicted = q.push(_env(e, message_id="halt", priority=0))
    assert evicted is not None
    assert evicted.message_id == "new-background"
    assert q.peek().message_id == "halt"


def test_dedupe_window_rejects_same_key_inside_window_and_allows_after():
    _, _, rt = _modules()
    d = rt.DedupeWindow(window_ms=500)
    assert d.accept("abc", 1000) is True
    assert d.accept("abc", 1200) is False
    assert d.accept("abc", 1600) is True


def test_dedupe_window_rejects_invalid_key_and_clock_regression():
    _, _, rt = _modules()
    d = rt.DedupeWindow(window_ms=500)
    with pytest.raises(ValueError, match="INVALID_DEDUPE_KEY"):
        d.accept("", 1000)
    with pytest.raises(ValueError, match="INVALID_DEDUPE_TIME"):
        d.accept("abc", True)
    assert d.accept("abc", 1000) is True
    with pytest.raises(ValueError, match="DEDUPE_CLOCK_REGRESSION"):
        d.accept("def", 999)
