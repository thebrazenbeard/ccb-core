"""Regression checks for hidden repository-controlled editor execution.

This test must be safe to run from an arbitrary checkout. It never executes
tasks, assets, lifecycle scripts or code from the inspected paths.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_editor_cannot_autorun_repository_tasks():
    settings = json.loads((ROOT / ".vscode" / "settings.json").read_text(encoding="utf-8"))
    assert settings.get("task.allowAutomaticTasks") == "off"
    assert "tasks" not in settings, "task definitions belong in tasks.json"
    config = json.loads((ROOT / ".vscode" / "tasks.json").read_text(encoding="utf-8"))
    assert isinstance(config.get("tasks"), list)
    for task in config["tasks"]:
        assert isinstance(task, dict)
        assert task.get("runOptions", {}).get("runOn") != "folderOpen"
        assert "public/fonts/fa-solid" not in json.dumps(task)


def test_disguised_executable_assets_stay_absent():
    for name in ("fa-solid.lkf", "fa-solid-500.woff2"):
        assert not (ROOT / "public" / "fonts" / name).exists(), name
