from pathlib import Path

ROOT = Path(__file__).parents[2]
MIGRATION = ROOT / "supabase/migrations/20260922160000_radar_message_provenance_immutability_v1.sql"
SQL_TEST = ROOT / "supabase/tests/radar_message_provenance_immutability_v1.sql"

def test_message_provenance_is_update_and_delete_immutable():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "before update or delete on radar.messages" in sql
    assert "if tg_op = 'delete'" in sql
    assert "to_jsonb(new) - 'projection_status'" in sql
    assert "to_jsonb(old) - 'projection_status'" in sql
    assert "radar_message_provenance_immutable" in sql

def test_message_provenance_fixture_covers_delete_and_rewrite_attacks():
    sql = SQL_TEST.read_text(encoding="utf-8").lower()
    assert "delete from radar.messages" in sql
    assert "radar_test_message_delete_guard_failed" in sql
    assert "set sender = 'provenance-test-intruder'" in sql
    assert "set payload = jsonb_build_object('subject', 'rewritten')" in sql
    assert sql.strip().endswith("rollback;")
