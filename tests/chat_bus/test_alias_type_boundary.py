import pytest

from chat_bus.identity import IdentityError, LogicalEndpoint


@pytest.mark.parametrize("aliases", ["ops", b"ops", 123, None])
def test_aliases_must_be_explicit_iterable_of_strings(aliases):
    with pytest.raises(IdentityError, match="ALIASES_MUST_BE_ITERABLE"):
        LogicalEndpoint.create("vera", "Vera", "bt2", aliases=aliases)


def test_aliases_can_be_tuple_of_strings():
    endpoint = LogicalEndpoint.create("vera", "Vera", "bt2", aliases=("ops", "@control"))
    assert endpoint.addresses == ("vera", "ops", "control")
