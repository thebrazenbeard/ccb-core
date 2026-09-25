from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "radar_trusted_project.py"

def test_trusted_projector_cli_is_provider_neutral_ccb_base_entrypoint():
    assert SCRIPT.exists()
    source = SCRIPT.read_text(encoding="utf-8")
    assert "supabase.co" not in source
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--endpoint" in result.stdout
