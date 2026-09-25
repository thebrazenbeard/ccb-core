from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "radar_validate_writer_lane.py"

def test_writer_lane_validator_cli_is_part_of_ccb_base():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--topology" in result.stdout
    assert "--cutover" in result.stdout
    assert "--bootstrap-main" in result.stdout
