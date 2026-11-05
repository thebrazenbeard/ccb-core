"""Public/private Bus source-custody regression, without provider access."""

from pathlib import Path
import unittest


_ROOT = Path(__file__).resolve().parents[2]


class ProtocolCurrentnessTests(unittest.TestCase):
    def test_historical_hub_directive_is_explicitly_quarantined(self):
        protocol = (_ROOT / "docs/protocol/PROTOCOL_V2.md").read_text(
            encoding="utf-8"
        )
        notice = "**Currentness correction (2026-11-05; source-only):**"
        self.assertIn(notice, protocol)
        self.assertLess(protocol.index(notice), protocol.index("## Messaging"))
        self.assertIn("private delivery channel remains **UNQUALIFIED**", protocol)
        self.assertIn("sanitized coordination and source references only", protocol)

    def test_canonical_repository_roles_keep_private_overlay_separate(self):
        roles = (_ROOT / "docs/operator/REPOSITORY_ROLES.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("`thebrazenbeard/chat-communication-bus` is **public**", roles)
        self.assertIn("separately access-controlled overlays", roles)


if __name__ == "__main__":
    unittest.main()
