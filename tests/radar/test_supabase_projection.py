import importlib
import pytest


def _module():
    try:
        return importlib.import_module("radar.supabase_projection")
    except Exception as exc:
        pytest.fail(f"Radar Supabase projection implementation missing: {exc}")


def test_projection_commands_are_parameterized_and_schema_scoped():
    m = _module()
    commands = m.build_projection_commands({"identities": [{"identity_id": "radar", "display_name": "Radar"}]})
    assert commands
    command = commands[0]
    assert command.operation == "UPSERT_IDENTITY"
    assert command.schema == "radar"
    assert command.parameters["identity_id"] == "radar"
    assert "Radar" not in command.sql


def test_projection_generates_endpoint_and_subscription_repairs_without_literal_values():
    m = _module()
    commands = m.build_projection_commands(
        {
            "endpoints": [
                {
                    "endpoint_id": "radar-github-mailbox",
                    "identity_id": "radar",
                    "transport": "github_branch",
                    "address": "bus/radar-v1",
                    "status": "ACTIVE",
                }
            ],
            "subscriptions": [
                {
                    "identity_id": "radar",
                    "domain": "repository",
                    "intent": None,
                    "min_priority": 4,
                    "active": True,
                }
            ],
        }
    )
    operations = {c.operation for c in commands}
    assert operations == {"UPSERT_ENDPOINT", "UPSERT_SUBSCRIPTION"}
    assert all(c.schema == "radar" for c in commands)
    rendered = "\n".join(c.sql for c in commands)
    assert "bus/radar-v1" not in rendered
    assert "repository" not in rendered
    endpoint = next(c for c in commands if c.operation == "UPSERT_ENDPOINT")
    subscription = next(c for c in commands if c.operation == "UPSERT_SUBSCRIPTION")
    assert endpoint.parameters["address"] == "bus/radar-v1"
    assert subscription.parameters["domain"] == "repository"


def test_projection_contains_no_credentials():
    m = _module()
    commands = m.build_projection_commands(
        {
            "identities": [{"identity_id": "radar", "display_name": "Radar"}],
            "endpoints": [{"endpoint_id": "e", "identity_id": "radar", "transport": "github", "address": "repo"}],
        }
    )
    rendered = "\n".join(c.sql for c in commands).lower()
    assert "service_role" not in rendered
    assert "apikey" not in rendered
    assert "secret" not in rendered


def test_provider_snapshot_parser_keeps_provider_state_as_projection():
    m = _module()
    snapshot = m.parse_provider_snapshot({"protocol_head": "abc", "identities": [{"identity_id": "radar"}]})
    assert snapshot.protocol_head == "abc"
    assert snapshot.canonical is False


@pytest.mark.parametrize("value", [True, 1.0, 1.9, "1", "urgent"])
def test_invalid_subscription_priority_uses_projection_error_contract(value):
    m = _module()
    with pytest.raises(m.ProjectionError) as exc_info:
        m.build_projection_commands(
            {
                "subscriptions": [
                    {
                        "identity_id": "radar",
                        "domain": "system",
                        "min_priority": value,
                        "active": True,
                    }
                ]
            }
        )
    assert exc_info.value.code == "INVALID_PRIORITY"


@pytest.mark.parametrize(
    ("domain", "intent", "expected_code"),
    [
        ("not-a-domain", None, "UNKNOWN_DOMAIN"),
        ("system", "not-an-intent", "UNKNOWN_INTENT"),
        ("system", "", "INVALID_INTENT"),
    ],
)
def test_projected_subscription_taxonomy_fails_closed(domain, intent, expected_code):
    m = _module()
    with pytest.raises(m.ProjectionError) as exc_info:
        m.build_projection_commands(
            {
                "subscriptions": [
                    {
                        "identity_id": "radar",
                        "domain": domain,
                        "intent": intent,
                        "min_priority": 3,
                        "active": True,
                    }
                ]
            }
        )
    assert exc_info.value.code == expected_code


def test_subscription_active_must_be_boolean_not_truthy_string():
    m = _module()
    with pytest.raises(m.ProjectionError) as exc_info:
        m.build_projection_commands(
            {
                "subscriptions": [
                    {
                        "identity_id": "radar",
                        "domain": "system",
                        "min_priority": 3,
                        "active": "false",
                    }
                ]
            }
        )
    assert exc_info.value.code == "INVALID_ACTIVE"


def test_endpoint_status_must_match_provider_constraint():
    m = _module()
    with pytest.raises(m.ProjectionError) as exc_info:
        m.build_projection_commands(
            {
                "endpoints": [
                    {
                        "endpoint_id": "e",
                        "identity_id": "radar",
                        "transport": "github",
                        "address": "repo",
                        "status": "MAYBE",
                    }
                ]
            }
        )
    assert exc_info.value.code == "INVALID_ENDPOINT_STATUS"


@pytest.mark.parametrize("field", ["identities", "endpoints", "subscriptions"])
def test_projection_collections_reject_scalar_strings(field):
    m = _module()
    with pytest.raises(m.ProjectionError) as exc_info:
        m.build_projection_commands({field: "radar"})
    assert exc_info.value.code == f"INVALID_{field.upper()}"
