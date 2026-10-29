"""Source and runnable redaction controls for handled Edge Function 503 logs.

This does not claim a deployed Edge Function or live provider recovery.
"""

from pathlib import Path
import re
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
FUNCTION = ROOT / "supabase" / "functions" / "github-bus-ingest"
EXPECTED = {
    "OIDC_JWKS_UNAVAILABLE",
    "OIDC_JWKS_INVALID",
    "TRUSTED_PROJECTOR_SHA_INVALID",
    "PROJECT_CONCURRENCY_INVALID",
    "PROVIDER_CONFIGURATION_MISSING",
}


class Provider503LoggingContractTests(unittest.TestCase):
    def test_handler_logs_only_diagnostic_helper_on_handled_503(self):
        source = (FUNCTION / "handler.ts").read_text(encoding="utf-8")
        self.assertIn('from "./provider_503_log.ts"', source)
        self.assertIn("const diagnostic = provider503LogEvent(error.status, error.code);", source)
        self.assertIn("if (diagnostic !== null) console.warn(diagnostic);", source)
        self.assertRegex(source,
        r"if \(error instanceof HttpError\) \{\s*"
        r"const diagnostic = provider503LogEvent\(error.status, error.code\);\s*"
        r"if \(diagnostic !== null\) console.warn\(diagnostic\);\s*"
        r"return response\(error.status, \{ ok: false, error: error.code \}\);",
        )
        self.assertNotIn("console.warn(error)", source)
        self.assertNotIn("console.warn(req", source)
        self.assertIn('console.error("RADAR_PROVIDER_INTERNAL_ERROR");', source)
        self.assertNotIn("console.error(error)", source)


    def test_503_diagnostic_is_five_code_allowlist(self):
        source = (FUNCTION / "provider_503_log.ts").read_text(encoding="utf-8")
        members = set(re.findall(r'^\s+"([A-Z_]+)",?$', source, flags=re.MULTILINE))
        self.assertEqual(members, EXPECTED)
        self.assertIn('return `RADAR_PROVIDER_503:${safeCode}`;', source)
        self.assertIn('return null;', source)
        self.assertIn('"UNKNOWN"', source)
        for prohibited in ("req.", "headers", "authorization", "token", "body", "url", "Deno.env"):
            self.assertNotIn(prohibited, source)


    def test_runtime_helper_redacts_untrusted_code_and_ignores_other_status(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js unavailable; source checks still run")

        code = """
import assert from 'node:assert/strict';
import { provider503LogEvent } from './supabase/functions/github-bus-ingest/provider_503_log.ts';
const valid = [
  'OIDC_JWKS_UNAVAILABLE', 'OIDC_JWKS_INVALID',
  'TRUSTED_PROJECTOR_SHA_INVALID', 'PROJECT_CONCURRENCY_INVALID',
  'PROVIDER_CONFIGURATION_MISSING',
];
for (const value of valid) {
  assert.equal(provider503LogEvent(503, value), `RADAR_PROVIDER_503:${value}`);
}
for (const untrusted of [
  'Bearer SECRET_DO_NOT_LOG', 'REQUEST_BODY=SECRET',
  'https://private.example/secret', null, undefined, {}, 503,
]) {
  assert.equal(provider503LogEvent(503, untrusted), 'RADAR_PROVIDER_503:UNKNOWN');
}
assert.equal(provider503LogEvent(500, 'OIDC_JWKS_UNAVAILABLE'), null);
assert.equal(provider503LogEvent(401, 'PROVIDER_CONFIGURATION_MISSING'), null);
"""
        result = subprocess.run(
        [node, "--input-type=module", "--eval", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        )
        if result.returncode != 0 and "Unknown file extension" in result.stderr:
            self.skipTest("Node.js does not support native TypeScript execution")
        self.assertEqual(result.returncode, 0, result.stderr)
