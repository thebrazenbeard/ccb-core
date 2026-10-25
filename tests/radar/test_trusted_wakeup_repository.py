"""The trusted projector must bind the caller to the deployment topology."""

import json

import pytest

from radar.trusted_reconciler import ReconcileError
from radar.trusted_wakeup import requested_identities


@pytest.fixture
def topology(tmp_path):
    path = tmp_path / "topology.json"
    path.write_text(
        json.dumps(
            {
                "repository": "thebrazenbeard/chat-communication-bus",
                "required_writer_lanes": {
                    "one": {
                        "writer": "One",
                        "branch": "bus/one-v2",
                        "lifecycle": "ACTIVE",
                    },
                    "parallax": {
                        "writer": "Parallax",
                        "branch": "bus/parallax-v1",
                        "lifecycle": "REGISTERED_INERT",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_private_overlay_schedule_admits_only_active_lanes(topology):
    result = requested_identities(
        event_name="schedule",
        event={},
        topology_path=topology,
        repository="thebrazenbeard/chat-communication-bus",
    )
    assert result == (("one",), None)


def test_private_overlay_workflow_run_routes_active_branch(topology):
    result = requested_identities(
        event_name="workflow_run",
        event={"workflow_run": {"head_branch": "bus/one-v2", "head_sha": "a" * 40}},
        topology_path=topology,
        repository="thebrazenbeard/chat-communication-bus",
    )
    assert result == (("one",), "a" * 40)


@pytest.mark.parametrize("repository", ["example/ccb-core", "someone/another-repo", ""])
def test_rejects_caller_repository_not_in_trusted_topology(topology, repository):
    with pytest.raises(ReconcileError, match="UNTRUSTED_CALLER_REPOSITORY"):
        requested_identities(
            event_name="schedule", event={}, topology_path=topology,
            repository=repository,
        )


@pytest.mark.parametrize("bad_value", [None, 12, {}, "", "no-slash"])
def test_missing_or_invalid_topology_repository_fails_closed(topology, bad_value):
    obj = json.loads(topology.read_text(encoding="utf-8"))
    obj["repository"] = bad_value
    topology.write_text(json.dumps(obj), encoding="utf-8")
    with pytest.raises(ReconcileError, match="TRUSTED_TOPOLOGY_REPOSITORY_INVALID"):
        requested_identities(
            event_name="schedule", event={}, topology_path=topology,
            repository="thebrazenbeard/chat-communication-bus",
        )
