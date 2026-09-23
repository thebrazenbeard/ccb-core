from radar.reconciliation_journal import ProjectionJournal


def test_control_cut_is_lane_scoped_not_global() -> None:
    journal = ProjectionJournal()
    journal.seed_lane_state("yin", "bus/yin-v2", "a0")
    journal.seed_lane_state("yang", "bus/yang-v2", "b0")
    journal.set_control_cut(
        identity="yin",
        branch="bus/yin-v2",
        control_sha="control-yin",
        topology_sha="topology-yin",
    )
    journal.set_control_cut(
        identity="yang",
        branch="bus/yang-v2",
        control_sha="control-yang",
        topology_sha="topology-yang",
    )

    yin = journal.claim_batch(
        identity="yin",
        branch="bus/yin-v2",
        expected_projected_head="a0",
        target_head="a1",
        control_sha="control-yin",
        topology_sha="topology-yin",
        files=(),
        owner="worker-yin",
        now_ms=1_000,
        lease_ms=500,
    )
    yang = journal.claim_batch(
        identity="yang",
        branch="bus/yang-v2",
        expected_projected_head="b0",
        target_head="b1",
        control_sha="control-yang",
        topology_sha="topology-yang",
        files=(),
        owner="worker-yang",
        now_ms=1_000,
        lease_ms=500,
    )

    assert journal.finalize_batch(yin, now_ms=1_100).projected_head_sha == "a1"
    assert journal.finalize_batch(yang, now_ms=1_100).projected_head_sha == "b1"
