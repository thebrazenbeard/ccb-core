"""CCB endpoint identity must remain stable after registration."""
import pytest
from chat_bus.identity import EndpointRegistry, IdentityError, LogicalEndpoint


def test_mutable_alias_input_cannot_change_endpoint_identity():
    aliases = ["alpha"]
    endpoint = LogicalEndpoint(
        endpoint_id="node", display_name="Node", project_scope="ops", aliases=aliases
    )
    registry = EndpointRegistry([endpoint])
    aliases.append("injected")
    assert endpoint.aliases == ("alpha",)
    assert registry.resolve("alpha") is endpoint
    assert registry.get("injected") is None


def test_generator_alias_input_is_snapshotted_once():
    incoming = (x for x in ["first", "second"])
    endpoint = LogicalEndpoint(
        endpoint_id="node", display_name="Node", project_scope="ops",
        aliases=incoming
    )
    assert endpoint.addresses == ("node", "first", "second")


def test_string_alias_input_is_not_split_into_character_aliases():
    with pytest.raises(IdentityError, match="INVALID_ENDPOINT_ALIASES"):
        LogicalEndpoint(endpoint_id="node", display_name="Node",
                        project_scope="ops", aliases="alpha")
