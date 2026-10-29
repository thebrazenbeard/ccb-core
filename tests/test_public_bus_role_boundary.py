"""Static guard: public Bus coordination must not be described as a private vault.

This checks documentation consistency only. It cannot erase previously published
history, verify hosted custody, or authorize a private provider effect.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_public_bus_role_custody_is_consistent_across_operator_docs():
    docs = (
        ROOT / "README.md",
        ROOT / "PUBLIC_CORE_BOUNDARY.md",
        ROOT / "docs/operator/REPOSITORY_ROLES.md",
    )
    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert "chat-communication-bus" in text, path
        assert "public" in text.lower(), path
        assert "separately access-controlled" in text or "separately access-controlled" in text.lower(), path
        assert "private branch vault" not in text, path
        assert "may retain private branches and overlays" not in text, path
        assert "is a private branch vault" not in text, path
