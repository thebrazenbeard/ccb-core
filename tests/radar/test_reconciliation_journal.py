from __future__ import annotations

import pytest

from radar.reconciliation_journal import (
    BatchFileState,
    JournalError,
    ProjectionJournal,
)


def _claimed_journal():
    journal = ProjectionJournal()
    journal.seed_lane_state("yin", "bus/yin-v2", "a0")
    journal.set_control_cut(
        identity="yin",
        branch="bus/yin-v2",
        control_sha="control-1",
        topology_sha="topology-1",
    )
    claim = journal.claim_batch(
        identity="yin",
        branch="bus/yin-v2",
        expected_projected_head="a0",
        target_head="a2",
        control_sha="control-1",
        topology_sha="topology-1",
        files=("messages/yin-0001.md", "messages/yin-0002.md"),
        owner="worker-a",
        now_ms=1_000,
        lease_ms=500,
    )
    return journal, claim


def test_claim_cannot_invent_lane_state():
    journal = ProjectionJournal()
    with pytest.raises(JournalError, match="LANE_UNSEEDED"):
        journal.claim_batch(
            identity="yin",
            branch="bus/yin-v2",
            expected_projected_head=None,
            target_head="a0",
            control_sha="control-1",
            topology_sha="topology-1",
            files=(),
            owner="worker-a",
            now_ms=1_000,
            lease_ms=500,
        )


def test_zero_file_bootstrap_can_initialize_cursor_after_explicit_seed():
    journal = ProjectionJournal()
    journal.seed_lane_state("yin", "bus/yin-v2", None)
    journal.set_control_cut(
        identity="yin",
        branch="bus/yin-v2",
        control_sha="control-1",
        topology_sha="topology-1",
    )
    claim = journal.claim_batch(
        identity="yin",
        branch="bus/yin-v2",
        expected_projected_head=None,
        target_head="a0",
        control_sha="control-1",
        topology_sha="topology-1",
        files=(),
        owner="worker-a",
        now_ms=1_000,
        lease_ms=500,
    )
    assert journal.finalize_batch(claim, now_ms=1_100).projected_head_sha == "a0"


def test_finalize_advances_cursor_only_when_every_file_is_terminal():
    journal, claim = _claimed_journal()
    journal.record_file_result(claim, "messages/yin-0001.md", BatchFileState.PROJECTED)

    with pytest.raises(JournalError, match="UNRESOLVED_BATCH_FILES"):
        journal.finalize_batch(claim, now_ms=1_100)

    assert journal.projected_head("yin", "bus/yin-v2") == "a0"

    journal.record_file_result(claim, "messages/yin-0002.md", BatchFileState.IDEMPOTENT)
    finalized = journal.finalize_batch(claim, now_ms=1_200)

    assert finalized.projected_head_sha == "a2"
    assert journal.projected_head("yin", "bus/yin-v2") == "a2"


def test_expired_claim_cannot_finalize_after_last_file_result():
    journal, claim = _claimed_journal()
    journal.record_file_result(claim, "messages/yin-0001.md", BatchFileState.PROJECTED)
    journal.record_file_result(claim, "messages/yin-0002.md", BatchFileState.PROJECTED)

    with pytest.raises(JournalError, match="STALE_CLAIM"):
        journal.finalize_batch(claim, now_ms=1_501)

    assert journal.projected_head("yin", "bus/yin-v2") == "a0"


def test_competing_takeover_fences_stale_worker():
    journal, first = _claimed_journal()
    second = journal.claim_batch(
        identity="yin",
        branch="bus/yin-v2",
        expected_projected_head="a0",
        target_head="a2",
        control_sha="control-1",
        topology_sha="topology-1",
        files=("messages/yin-0001.md", "messages/yin-0002.md"),
        owner="worker-b",
        now_ms=1_600,
        lease_ms=500,
    )

    with pytest.raises(JournalError, match="STALE_CLAIM"):
        journal.record_file_result(first, "messages/yin-0001.md", BatchFileState.PROJECTED)

    for path in ("messages/yin-0001.md", "messages/yin-0002.md"):
        journal.record_file_result(second, path, BatchFileState.PROJECTED)
    journal.finalize_batch(second, now_ms=1_700)

    assert journal.projected_head("yin", "bus/yin-v2") == "a2"


def test_control_or_topology_cut_change_blocks_finalization():
    journal, claim = _claimed_journal()
    journal.record_file_result(claim, "messages/yin-0001.md", BatchFileState.PROJECTED)
    journal.record_file_result(claim, "messages/yin-0002.md", BatchFileState.PROJECTED)

    journal.set_control_cut(
        identity="yin",
        branch="bus/yin-v2",
        control_sha="control-2",
        topology_sha="topology-1",
    )
    with pytest.raises(JournalError, match="CONTROL_CUT_CHANGED"):
        journal.finalize_batch(claim, now_ms=1_200)

    assert journal.projected_head("yin", "bus/yin-v2") == "a0"


def test_cursor_advance_by_other_batch_blocks_stale_finalization():
    journal = ProjectionJournal()
    journal.seed_lane_state("yin", "bus/yin-v2", None)
    journal.set_control_cut(
        identity="yin",
        branch="bus/yin-v2",
        control_sha="control-1",
        topology_sha="topology-1",
    )
    first = journal.claim_batch(
        identity="yin",
        branch="bus/yin-v2",
        expected_projected_head=None,
        target_head="a1",
        control_sha="control-1",
        topology_sha="topology-1",
        files=("messages/yin-0001.md",),
        owner="worker-a",
        now_ms=1_000,
        lease_ms=500,
    )
    journal.record_file_result(first, "messages/yin-0001.md", BatchFileState.PROJECTED)
    journal.finalize_batch(first, now_ms=1_100)

    stale = journal.claim_batch(
        identity="yin",
        branch="bus/yin-v2",
        expected_projected_head="a1",
        target_head="a2",
        control_sha="control-1",
        topology_sha="topology-1",
        files=("messages/yin-0002.md",),
        owner="worker-a",
        now_ms=1_200,
        lease_ms=500,
    )
    journal.record_file_result(stale, "messages/yin-0002.md", BatchFileState.PROJECTED)
    journal.force_projected_head_for_test("yin", "bus/yin-v2", "a3")

    with pytest.raises(JournalError, match="PROJECTED_BASE_CHANGED"):
        journal.finalize_batch(stale, now_ms=1_300)

    assert journal.projected_head("yin", "bus/yin-v2") == "a3"


def test_lost_response_after_successful_finalize_is_idempotent():
    journal, claim = _claimed_journal()
    journal.record_file_result(claim, "messages/yin-0001.md", BatchFileState.PROJECTED)
    journal.record_file_result(claim, "messages/yin-0002.md", BatchFileState.IDEMPOTENT)

    first = journal.finalize_batch(claim, now_ms=1_200)
    replay = journal.finalize_batch(claim, now_ms=1_250)

    assert replay == first
    assert journal.projected_head("yin", "bus/yin-v2") == "a2"
