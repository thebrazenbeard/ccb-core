from __future__ import annotations

import threading

import pytest

from radar.envelope import RadarEnvelope
from radar.model import NodeStatus
from radar.registry import IdentityRegistry, RegistryError
from radar.routing import Router


def _env(*, audience=("yin",), intent="event", priority=2):
    result = RadarEnvelope.from_mapping(
        {
            "schema_version": 1,
            "message_id": f"radar-node-route-{intent}-{priority}",
            "created_at": "2026-09-06T13:23:00Z",
            "sender": "radar",
            "audience": list(audience),
            "domain": "database",
            "intent": intent,
            "priority": priority,
            "payload": {"intent": intent},
        }
    )
    assert result.envelope is not None
    return result.envelope


def _register_node(
    registry: IdentityRegistry,
    node_id: str,
    *,
    status=NodeStatus.ONLINE,
    last_heartbeat_ms=1_000,
    lease_expires_ms=2_000,
):
    return registry.register_node(
        node_id,
        identity_id="yin",
        status=status,
        last_heartbeat_ms=last_heartbeat_ms,
        lease_expires_ms=lease_expires_ms,
    )


def test_routes_only_exact_live_node_matching_domain_and_intent():
    registry = IdentityRegistry()
    registry.register_identity("yin", display_name="Yin")
    _register_node(registry, "yin-event")
    _register_node(registry, "yin-command")
    registry.subscribe("yin-event", domain="database", intent="event", min_priority=4)
    registry.subscribe("yin-command", domain="database", intent="command", min_priority=4)

    decision = Router(registry).route(_env(intent="event"), now_ms=1_500)

    assert decision.recipients == ("yin-event",)


def test_missing_or_null_lease_is_not_routable():
    registry = IdentityRegistry()
    registry.register_identity("yin", display_name="Yin")
    _register_node(
        registry,
        "yin-no-lease",
        last_heartbeat_ms=None,
        lease_expires_ms=None,
    )
    registry.subscribe("yin-no-lease", domain="database", intent="event", min_priority=4)

    assert Router(registry).route(_env(), now_ms=1_500).recipients == ()


def test_expired_lease_is_not_routable():
    registry = IdentityRegistry()
    registry.register_identity("yin", display_name="Yin")
    _register_node(registry, "yin-expired", lease_expires_ms=1_499)
    registry.subscribe("yin-expired", domain="database", intent="event", min_priority=4)

    assert Router(registry).route(_env(), now_ms=1_500).recipients == ()


def test_paused_and_offline_nodes_are_not_routable_even_with_live_lease():
    registry = IdentityRegistry()
    registry.register_identity("yin", display_name="Yin")
    _register_node(registry, "yin-paused", status=NodeStatus.PAUSED)
    _register_node(registry, "yin-offline", status=NodeStatus.OFFLINE)
    registry.subscribe("yin-paused", domain="database", intent="event", min_priority=4)
    registry.subscribe("yin-offline", domain="database", intent="event", min_priority=4)

    assert Router(registry).route(_env(), now_ms=1_500).recipients == ()


def test_wildcard_intent_subscription_matches_any_intent_on_that_node_only():
    registry = IdentityRegistry()
    registry.register_identity("yin", display_name="Yin")
    _register_node(registry, "yin-any")
    _register_node(registry, "yin-event")
    registry.subscribe("yin-any", domain="database", intent=None, min_priority=4)
    registry.subscribe("yin-event", domain="database", intent="event", min_priority=4)

    decision = Router(registry).route(_env(intent="command"), now_ms=1_500)

    assert decision.recipients == ("yin-any",)


def test_identity_named_subscription_is_only_compatibly_bound_when_unambiguous():
    registry = IdentityRegistry()
    registry.register_identity("yin", display_name="Yin")
    _register_node(registry, "yin-one")
    sub = registry.subscribe("yin", domain="database", intent="event", min_priority=4)
    assert sub.node_id == "yin-one"

    _register_node(registry, "yin-two")
    with pytest.raises(RegistryError, match="AMBIGUOUS_SUBSCRIPTION_TARGET"):
        registry.subscribe("yin", domain="database", intent="command", min_priority=4)


def test_identity_named_subscription_binding_is_atomic_with_node_registration():
    resolved = threading.Event()
    release_resolution = threading.Event()
    second_registered = threading.Event()

    class PausingRegistry(IdentityRegistry):
        def _resolve_subscription_node(self, target_id):
            node = super()._resolve_subscription_node(target_id)
            if target_id == "yin":
                resolved.set()
                assert release_resolution.wait(5), "timed out releasing subscription resolution"
            return node

    registry = PausingRegistry()
    registry.register_identity("yin", display_name="Yin")
    _register_node(registry, "yin-one")
    outcomes = {}

    def subscribe_identity():
        outcomes["subscription"] = registry.subscribe(
            "yin", domain="database", intent="event", min_priority=4
        )

    def register_second():
        try:
            _register_node(registry, "yin-two")
        finally:
            second_registered.set()

    subscribe_thread = threading.Thread(target=subscribe_identity)
    subscribe_thread.start()
    assert resolved.wait(5), "subscription did not resolve target"

    registration_thread = threading.Thread(target=register_second)
    registration_thread.start()
    registered_before_subscription_commit = second_registered.wait(0.25)

    release_resolution.set()
    subscribe_thread.join(5)
    registration_thread.join(5)

    assert not subscribe_thread.is_alive()
    assert not registration_thread.is_alive()
    assert registered_before_subscription_commit is False
    assert outcomes["subscription"].node_id == "yin-one"
    assert registry.node("yin-two").identity_id == "yin"


def test_route_eligibility_snapshot_serializes_against_status_change():
    subscription_checked = threading.Event()
    release_route = threading.Event()
    status_done = threading.Event()

    class PausingRegistry(IdentityRegistry):
        def node_is_subscribed(self, *args, **kwargs):
            result = super().node_is_subscribed(*args, **kwargs)
            subscription_checked.set()
            assert release_route.wait(5), "timed out releasing route snapshot"
            return result

    registry = PausingRegistry()
    registry.register_identity("yin", display_name="Yin")
    _register_node(registry, "yin-event")
    registry.subscribe("yin-event", domain="database", intent="event", min_priority=4)
    outcomes = {}

    def route_message():
        outcomes["decision"] = Router(registry).route(_env(), now_ms=1_500)

    def mark_offline():
        try:
            registry.set_node_status("yin-event", NodeStatus.OFFLINE)
        finally:
            status_done.set()

    route_thread = threading.Thread(target=route_message)
    route_thread.start()
    assert subscription_checked.wait(5), "route did not reach subscription check"

    status_thread = threading.Thread(target=mark_offline)
    status_thread.start()
    status_finished_inside_route_snapshot = status_done.wait(0.25)

    release_route.set()
    route_thread.join(5)
    status_thread.join(5)

    assert not route_thread.is_alive()
    assert not status_thread.is_alive()
    assert status_finished_inside_route_snapshot is False
    assert outcomes["decision"].recipients == ("yin-event",)
    assert registry.node("yin-event").status is NodeStatus.OFFLINE


def test_invalid_node_lease_times_fail_closed():
    registry = IdentityRegistry()
    registry.register_identity("yin", display_name="Yin")

    with pytest.raises(RegistryError, match="INVALID_NODE_LEASE_TIME"):
        _register_node(registry, "yin-bool-heartbeat", last_heartbeat_ms=True)
    with pytest.raises(RegistryError, match="INVALID_NODE_LEASE_TIME"):
        _register_node(
            registry,
            "yin-backwards-lease",
            last_heartbeat_ms=1_000,
            lease_expires_ms=999,
        )


def test_route_time_rejects_boolean_int_coercion():
    registry = IdentityRegistry()
    registry.register_identity("yin", display_name="Yin")
    _register_node(registry, "yin-event")
    registry.subscribe("yin-event", domain="database", intent="event", min_priority=4)

    with pytest.raises(ValueError, match="INVALID_ROUTE_TIME"):
        Router(registry).route(_env(), now_ms=True)
