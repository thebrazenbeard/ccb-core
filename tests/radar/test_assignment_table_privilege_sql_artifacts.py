[Reading 16 lines from start (total: 16 lines, 0 remaining)]

from pathlib import Path

ROOT = Path(__file__).parents[2]
MIGRATION = ROOT / "supabase/migrations/20260922163000_radar_assignment_table_privilege_v1.sql"
SQL_TEST = ROOT / "supabase/tests/radar_assignment_table_privilege_v1.sql"

def test_assignment_tables_are_function_only_for_service_role():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    for table in ("assignments", "assignment_events"):
        assert f"revoke insert, update, delete on table radar.{table} from service_role" in sql
        assert f"grant select on table radar.{table} to service_role" in sql

def test_assignment_fixture_retains_reconciliation_rpc():
    sql = SQL_TEST.read_text(encoding="utf-8").lower()
    assert "'radar.reconcile_bus_assignments_v1()'" in sql
    assert sql.strip().endswith("rollback;")