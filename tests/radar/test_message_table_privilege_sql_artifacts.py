[Reading 15 lines from start (total: 15 lines, 0 remaining)]

from pathlib import Path

ROOT = Path(__file__).parents[2]
MIGRATION = ROOT / "supabase/migrations/20260922164500_radar_message_table_privilege_v1.sql"
SQL_TEST = ROOT / "supabase/tests/radar_message_table_privilege_v1.sql"

def test_messages_are_rpc_only_for_service_role():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "revoke insert, update, delete on table radar.messages from service_role" in sql
    assert "grant select on table radar.messages to service_role" in sql

def test_message_fixture_retains_projection_rpc():
    sql = SQL_TEST.read_text(encoding="utf-8").lower()
    assert "radar.project_bus_message_v1(text,timestamptz,text,text[],boolean,text[],text,text,jsonb)" in sql
    assert sql.strip().endswith("rollback;")