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


@pytest.mark.parametrize(
    "event_name",
    ["workflow_dispatch", "push", "repository_dispatch", "pull_request"],
)
def test_public_transition_manual_and_untrusted_triggers_remain_held(
    topology, event_name
):
    """Manual entry into a reusable projector is not provider authorization.

    Even a fully shaped workflow_run payload cannot turn a different GitHub
    trigger into an eligible wakeup. The public dispatcher has an intentional
    manual-only hold; this source gate still rejects that trigger until a
    separately reviewed release changes its trust and authority contract.
    """
    event = {
        "repository": {"full_name": "thebrazenbeard/chat-communication-bus"},
        "ref": "refs/heads/main",
        "workflow_run": {"head_branch": "bus/one-v2", "head_sha": "a" * 40},
    }
    with pytest.raises(ReconcileError, match="^UNSUPPORTED_CALLER_EVENT$"):
        requested_identities(
            event_name=event_name,
            event=event,
            topology_path=topology,
            repository="thebrazenbeard/chat-communication-bus",
        )


@pytest.mark.parametrize(
    "event",
    [
        {},
        {"workflow_run": {"head_branch": "bus/one-v2", "head_sha": "Z" * 40}},
        {"workflow_run": {"head_branch": "bus/one-v2", "head_sha": "a" * 39}},
        {"workflow_run": {"head_branch": "bus/parallax-v1", "head_sha": "a" * 40}},
    ],
)
def test_malformed_or_inert_workflow_run_cannot_select_writer_lane(topology, event):
    with pytest.raises(
        ReconcileError,
        match="^(WORKFLOW_RUN_EVENT_INVALID|WORKFLOW_RUN_HEAD_INVALID|WAKEUP_BRANCH_NOT_ACTIVE)$",
    ):
        requested_identities(
            event_name="workflow_run",
            event=event,
            topology_path=topology,
            repository="thebrazenbeard/chat-communication-bus",
        )


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
