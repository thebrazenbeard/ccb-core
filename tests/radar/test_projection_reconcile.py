import importlib
import pytest


def _mods():
    try:
        return importlib.import_module("radar.github_projection"), importlib.import_module("radar.reconcile")
    except Exception as exc:
        pytest.fail(f"Radar projection implementation missing: {exc}")


def test_branch_projection_normalizes_brigit_alias_without_erasing_provenance():
    gp, _ = _mods()
    p = gp.project_branches(["bus/beta-legacy-v1", "bus/yin-v1", "bus/protocol-v2"], required_identities={"beta", "yin"})
    assert p.identity_lanes["beta"] == "bus/beta-legacy-v1"
    assert p.missing_required == ()
    assert "bus/beta-legacy-v1" in p.observed_branches


def test_missing_required_identity_lane_is_action_required():
    gp, _ = _mods()
    p = gp.project_branches(["bus/yin-v1"], required_identities={"yin", "seven"})
    assert p.missing_required == ("seven",)


def test_reconciler_separates_safe_projection_drift_from_durable_conflict():
    _, r = _mods()
    findings = r.Reconciler().compare(
        github_state={"protocol_head": "abc", "lanes": {"yin": "bus/yin-v1"}},
        provider_state={"protocol_head": "old", "lanes": {}},
    )
    codes = {f.code: f for f in findings}
    assert codes["PROTOCOL_HEAD_DRIFT"].repair_class == "SAFE_PROJECTION_REPAIR"
    assert codes["MISSING_PROVIDER_LANE"].repair_class == "SAFE_PROJECTION_REPAIR"


def test_conflicting_writer_claim_is_not_auto_repaired():
    _, r = _mods()
    findings = r.Reconciler().compare(
        github_state={"protocol_head": "abc", "lanes": {"yin": "bus/yin-v1"}},
        provider_state={"protocol_head": "abc", "lanes": {"yin": ["bus/yin-v1", "bus/other-v1"]}},
    )
    f = next(x for x in findings if x.code == "MULTIPLE_WRITER_CLAIMS")
    assert f.repair_class == "AMBIGUOUS_DURABLE_CONFLICT"


def test_empty_provider_writer_claims_do_not_crash_reconciliation():
    _, r = _mods()
    findings = r.Reconciler().compare(
        github_state={"protocol_head": "abc", "lanes": {"yin": "bus/yin-v1"}},
        provider_state={"protocol_head": "abc", "lanes": {"yin": []}},
    )
    finding = next(x for x in findings if x.code == "LANE_PROJECTION_DRIFT")
    assert finding.repair_class == "SAFE_PROJECTION_REPAIR"


def test_unhashable_provider_writer_claims_are_reported_not_crashed():
    _, r = _mods()
    findings = r.Reconciler().compare(
        github_state={"protocol_head": "abc", "lanes": {"yin": "bus/yin-v1"}},
        provider_state={
            "protocol_head": "abc",
            "lanes": {"yin": [{"branch": "bus/yin-v1"}, {"branch": "bus/other-v1"}]},
        },
    )
    finding = next(x for x in findings if x.code == "MULTIPLE_WRITER_CLAIMS")
    assert finding.repair_class == "AMBIGUOUS_DURABLE_CONFLICT"
    assert "bus/other-v1" in finding.detail
    assert "bus/yin-v1" in finding.detail
