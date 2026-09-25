[Reading 417 lines from start (total: 417 lines, 0 remaining)]

"""Radar logical identity, node, endpoint, and subscription registry."""

from __future__ import annotations

from dataclasses import replace
import threading

from .envelope import ALLOWED_DOMAINS, ALLOWED_INTENTS
from .model import Endpoint, Identity, Node, NodeStatus, Subscription


ROUTABLE_NODE_STATUSES = frozenset(
    {
        NodeStatus.ONLINE,
        NodeStatus.IDLE,
        NodeStatus.WORKING,
        NodeStatus.WAITING_DEPENDENCY,
        NodeStatus.DEGRADED,
    }
)


class RegistryError(ValueError):
    """Errors raised by the registry use short string codes as messages."""


def normalize_identifier(value: str) -> str:
    """Normalize an identifier to stripped, casefolded text."""
    if not isinstance(value, str) or not value.strip():
        raise RegistryError("INVALID_IDENTIFIER")
    return value.strip().casefold()


def _priority_value(value: object) -> int:
    """Require an exact integer priority in Radar's closed 0..4 domain."""
    if type(value) is not int or not 0 <= value <= 4:
        raise RegistryError("INVALID_PRIORITY")
    return value


def _subscription_domain(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RegistryError("DOMAIN_REQUIRED")
    domain = value.strip().casefold()
    if domain not in ALLOWED_DOMAINS:
        raise RegistryError("UNKNOWN_DOMAIN")
    return domain


def _subscription_intent(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RegistryError("INTENT_REQUIRED")
    intent = value.strip().casefold()
    if intent not in ALLOWED_INTENTS:
        raise RegistryError("UNKNOWN_INTENT")
    return intent


def _millisecond_value(value: object, *, allow_none: bool = False) -> int | None:
    if value is None and allow_none:
        return None
    if type(value) is not int or value < 0:
        raise RegistryError("INVALID_NODE_LEASE_TIME")
    return value


class IdentityRegistry:
    """Thread-safe registry for identities, nodes, endpoints and subscriptions."""

    def __init__(self) -> None:
        self._identities: dict[str, Identity] = {}
        self._aliases: dict[str, str] = {}
        self._nodes: dict[str, Node] = {}
        self._endpoints: dict[str, Endpoint] = {}
        self._subscriptions: list[Subscription] = []
        self._lock = threading.RLock()

    def register_identity(
        self,
        identity_id: str,
        *,
        display_name: str,
        aliases: tuple[str, ...] = (),
    ) -> Identity:
        """Register an identity while making alias ownership atomic."""
        canonical = normalize_identifier(identity_id)
        if not isinstance(display_name, str) or not display_name.strip():
            raise RegistryError("DISPLAY_NAME_REQUIRED")

        normalized_aliases: list[str] = []
        for alias in aliases:
            normalized = normalize_identifier(alias)
            if normalized == canonical or normalized in normalized_aliases:
                raise RegistryError("ALIAS_COLLISION")
            normalized_aliases.append(normalized)

        identity = Identity(canonical, display_name.strip(), tuple(normalized_aliases))
        with self._lock:
            if canonical in self._identities or canonical in self._aliases:
                raise RegistryError("IDENTITY_COLLISION")
            for alias in normalized_aliases:
                if alias in self._identities or alias in self._aliases:
                    raise RegistryError("ALIAS_COLLISION")

            self._identities[canonical] = identity
            for alias in normalized_aliases:
                self._aliases[alias] = canonical
        return identity

    def resolve_identity_id(self, value: str) -> str:
        key = normalize_identifier(value)
        with self._lock:
            if key in self._identities:
                return key
            canonical = self._aliases.get(key)
            if canonical is None:
                raise RegistryError("UNKNOWN_IDENTITY")
            return canonical

    def identity(self, identity_id: str) -> Identity:
        canonical = self.resolve_identity_id(identity_id)
        with self._lock:
            try:
                return self._identities[canonical]
            except KeyError as exc:
                raise RegistryError("UNKNOWN_IDENTITY") from exc

    def register_node(
        self,
        node_id: str,
        *,
        identity_id: str,
        status: NodeStatus = NodeStatus.ONLINE,
        last_heartbeat_ms: int | None = None,
        lease_expires_ms: int | None = None,
    ) -> Node:
        canonical_node = normalize_identifier(node_id)
        canonical_identity = self.resolve_identity_id(identity_id)
        heartbeat = _millisecond_value(last_heartbeat_ms, allow_none=True)
        lease_expires = _millisecond_value(lease_expires_ms, allow_none=True)
        if heartbeat is not None and lease_expires is not None and lease_expires < heartbeat:
            raise RegistryError("INVALID_NODE_LEASE_TIME")
        with self._lock:
            if canonical_node in self._nodes:
                raise RegistryError("NODE_COLLISION")
            try:
                if not isinstance(status, NodeStatus):
                    status = NodeStatus(status)
            except Exception as exc:
                raise RegistryError("INVALID_NODE_STATUS") from exc
            node = Node(
                canonical_node,
                canonical_identity,
                status,
                heartbeat,
                lease_expires,
            )
            self._nodes[canonical_node] = node
        return node

    def node(self, node_id: str) -> Node:
        key = normalize_identifier(node_id)
        with self._lock:
            try:
                return self._nodes[key]
            except KeyError as exc:
                raise RegistryError("UNKNOWN_NODE") from exc

    def nodes_for_identity(self, identity_id: str) -> tuple[Node, ...]:
        canonical = self.resolve_identity_id(identity_id)
        with self._lock:
            nodes = [n for n in self._nodes.values() if n.identity_id == canonical]
        return tuple(sorted(nodes, key=lambda n: n.node_id))

    def node_is_routable(self, node_id: str, *, now_ms: int) -> bool:
        now = _millisecond_value(now_ms)
        node = self.node(node_id)
        return (
            node.status in ROUTABLE_NODE_STATUSES
            and node.last_heartbeat_ms is not None
            and node.lease_expires_ms is not None
            and node.last_heartbeat_ms <= now
            and now < node.lease_expires_ms
        )

    def identity_has_routable_node(self, identity_id: str, *, now_ms: int) -> bool:
        canonical = self.resolve_identity_id(identity_id)
        return any(
            self.node_is_routable(node.node_id, now_ms=now_ms)
            for node in self.nodes_for_identity(canonical)
        )

    def set_node_status(self, node_id: str, status: NodeStatus) -> Node:
        with self._lock:
            current = self.node(node_id)
            try:
                if not isinstance(status, NodeStatus):
                    status = NodeStatus(status)
            except Exception as exc:
                raise RegistryError("INVALID_NODE_STATUS") from exc
            updated = replace(current, status=status)
            self._nodes[current.node_id] = updated
        return updated

    def register_endpoint(
        self,
        endpoint_id: str,
        *,
        identity_id: str,
        transport: str,
        address: str,
    ) -> Endpoint:
        canonical_endpoint = normalize_identifier(endpoint_id)
        canonical_identity = self.resolve_identity_id(identity_id)
        if not isinstance(transport, str) or not transport.strip():
            raise RegistryError("TRANSPORT_REQUIRED")
        if not isinstance(address, str) or not address.strip():
            raise RegistryError("ADDRESS_REQUIRED")

        endpoint = Endpoint(
            canonical_endpoint,
            canonical_identity,
            transport.strip().casefold(),
            address.strip(),
        )
        with self._lock:
            if canonical_endpoint in self._endpoints:
                raise RegistryError("ENDPOINT_COLLISION")
            self._endpoints[canonical_endpoint] = endpoint
        return endpoint

    def endpoint(self, endpoint_id: str) -> Endpoint:
        key = normalize_identifier(endpoint_id)
        with self._lock:
            try:
                return self._endpoints[key]
            except KeyError as exc:
                raise RegistryError("UNKNOWN_ENDPOINT") from exc

    def _resolve_subscription_node(self, target_id: str) -> Node:
        key = normalize_identifier(target_id)
        with self._lock:
            direct = self._nodes.get(key)
            if direct is not None:
                return direct
        # Compatibility for configuration callers that still name an identity:
        # only an unambiguous single registered node may be selected. The
        # durable subscription itself remains node-bound.
        canonical_identity = self.resolve_identity_id(target_id)
        nodes = self.nodes_for_identity(canonical_identity)
        if len(nodes) != 1:
            raise RegistryError("AMBIGUOUS_SUBSCRIPTION_TARGET")
        return nodes[0]

    def subscribe(
        self,
        node_id: str,
        *,
        domain: str,
        intent: str | None = None,
        min_priority: int = 3,
        enabled: bool = True,
    ) -> Subscription:
        normalized_domain = _subscription_domain(domain)
        min_pr = _priority_value(min_priority)
        if not isinstance(enabled, bool):
            raise RegistryError("INVALID_SUBSCRIPTION_ENABLED")
        normalized_intent = _subscription_intent(intent)

        # Identity-name compatibility is safe only if target resolution and the
        # durable node-bound append observe one atomic registry state. The RLock
        # is re-entrant because resolution helpers also take it.
        with self._lock:
            node = self._resolve_subscription_node(node_id)
            sub = Subscription(
                node.node_id,
                node.identity_id,
                normalized_domain,
                normalized_intent,
                min_pr,
                enabled,
            )
            if sub not in self._subscriptions:
                self._subscriptions.append(sub)
        return sub

    def subscriptions_for_domain(
        self,
        domain: str,
        priority: int,
        *,
        intent: str | None = None,
    ) -> tuple[Subscription, ...]:
        """Return enabled node subscriptions accepting domain/intent/priority."""
        key = _subscription_domain(domain)
        pr = _priority_value(priority)
        normalized_intent = _subscription_intent(intent)
        with self._lock:
            return tuple(
                sub
                for sub in self._subscriptions
                if sub.enabled
                and sub.domain == key
                and pr <= sub.min_priority
                and (
                    normalized_intent is None
                    or sub.intent is None
                    or sub.intent == normalized_intent
                )
            )

    def node_is_subscribed(
        self,
        node_id: str,
        *,
        domain: str,
        intent: str,
        priority: int,
    ) -> bool:
        canonical_node = self.node(node_id).node_id
        return any(
            sub.node_id == canonical_node
            for sub in self.subscriptions_for_domain(
                domain,
                priority,
                intent=intent,
            )
        )

    def routable_subscriber_node_ids(
        self,
        *,
        domain: str,
        intent: str,
        priority: int,
        now_ms: int,
        identity_ids: tuple[str, ...] | None = None,
    ) -> tuple[str, ...]:
        """Return one atomic snapshot of exact routable subscriber nodes.

        When ``identity_ids`` is supplied, recipient ordering follows that
        canonical identity order with node IDs sorted within each identity.
        Without it, all nodes are considered in node-ID order. The registry lock
        is held across status/lease and subscription checks so concurrent state
        changes cannot interleave inside one route decision.
        """
        now = _millisecond_value(now_ms)
        canonical_identities: tuple[str, ...] | None = None
        if identity_ids is not None:
            seen: set[str] = set()
            ordered: list[str] = []
            for identity_id in identity_ids:
                canonical = self.resolve_identity_id(identity_id)
                if canonical not in seen:
                    seen.add(canonical)
                    ordered.append(canonical)
            canonical_identities = tuple(ordered)

        with self._lock:
            if canonical_identities is None:
                candidates = sorted(self._nodes.values(), key=lambda item: item.node_id)
            else:
                candidates = []
                for canonical_identity in canonical_identities:
                    candidates.extend(
                        sorted(
                            (
                                node
                                for node in self._nodes.values()
                                if node.identity_id == canonical_identity
                            ),
                            key=lambda item: item.node_id,
                        )
                    )

            recipients: list[str] = []
            for node in candidates:
                if not self.node_is_routable(node.node_id, now_ms=now):
                    continue
                if not self.node_is_subscribed(
                    node.node_id,
                    domain=domain,
                    intent=intent,
                    priority=priority,
                ):
                    continue
                recipients.append(node.node_id)
            return tuple(recipients)

    def identity_is_subscribed(
        self,
        identity_id: str,
        *,
        domain: str,
        priority: int,
        intent: str | None = None,
    ) -> bool:
        canonical = self.resolve_identity_id(identity_id)
        return any(
            sub.identity_id == canonical
            for sub in self.subscriptions_for_domain(domain, priority, intent=intent)
        )

    def identities(self) -> tuple[Identity, ...]:
        with self._lock:
            return tuple(self._identities[key] for key in sorted(self._identities))


__all__ = [
    "IdentityRegistry",
    "NodeStatus",
    "ROUTABLE_NODE_STATUSES",
    "RegistryError",
    "normalize_identifier",
]