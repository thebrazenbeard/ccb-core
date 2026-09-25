from pathlib import Path

ROOT = Path(__file__).parents[2]
EXPECTED = [
    "20260902120716_radar_control_plane_v1.sql",
    "20260902120826_radar_realtime_v1.sql",
    "20260902203610_radar_visual_identity_v1.sql",
    "20260902204759_radar_live_message_projection_v1.sql",
    "20260902231445_radar_live_message_collision_v1.sql",
    "20260903000603_radar_receipt_plane_v1.sql",
    "20260903001906_radar_assignment_plane_v1.sql",
    "20260903113314_radar_operator_views_v1.sql",
    "20260903214334_radar_thread_closure_v1.sql",
    "20260905230823_radar_oidc_replay_guard_v1.sql",
    "20260906133630_radar_node_routing_hardening_v1.sql",
    "20260906154101_radar_projection_journal_v1.sql",
    "20260906165117_radar_projection_journal_api_v1.sql",
    "20260908010500_radar_projection_atomic_file_v1.sql",
    "20260922160000_radar_message_provenance_immutability_v1.sql",
    "20260922163000_radar_assignment_table_privilege_v1.sql",
    "20260922164500_radar_message_table_privilege_v1.sql",
    "20260922170000_radar_evidence_table_privilege_v1.sql",
    "20260922171230_radar_oidc_replay_guard_rls_v1.sql",
]

def test_radar_migration_chain_is_complete_and_version_unique():
    names = sorted(p.name for p in (ROOT / "supabase/migrations").glob("*_radar_*.sql"))
    assert names == EXPECTED
    versions = [name.split("_", 1)[0] for name in names]
    assert len(versions) == len(set(versions))
