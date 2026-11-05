"""Untrusted provider error values must never become diagnostic text.

Source-only tests with injected responses; no credentials, network, or provider effects.
"""

from __future__ import annotations

import unittest

from radar.central_projection import CentralProjectionError, ProviderProjectionClient


class ProviderErrorBoundaryTests(unittest.TestCase):
    def _raise_from_response(self, response):
        client = ProviderProjectionClient(
            "https://synthetic-provider.example.invalid/ingest",
            token_provider=lambda _audience: "synthetic-only",
            transport=lambda _url, _body, *, token_provider: response,
        )
        with self.assertRaises(CentralProjectionError) as raised:
            client.get_lane("synthetic", "main")
        return str(raised.exception)

    def test_newlines_and_secret_marker_never_enter_diagnostic(self):
        detail = "SYNTHETIC_PRIVATE_MARKER\\nFORGED_LOG_LINE".replace("\\n", "\n")
        error = self._raise_from_response({"ok": False, "error": detail})
        self.assertEqual(error, "PROVIDER_OPERATION_FAILED:cursor:UNKNOWN")
        self.assertNotIn("SYNTHETIC_PRIVATE_MARKER", error)
        self.assertNotIn("\n", error)

    def test_oversized_and_nonstring_error_values_fail_opaque(self):
        for value in ("SYNTHETIC_PRIVATE_MARKER" * 10000, ["INTERNAL_ERROR"], None, 42, {"error": "INTERNAL_ERROR"}):
            with self.subTest(value_type=type(value).__name__):
                self.assertEqual(
                    self._raise_from_response({"ok": False, "error": value}),
                    "PROVIDER_OPERATION_FAILED:cursor:UNKNOWN",
                )

    def test_only_source_defined_error_codes_pass(self):
        for code in (
            "OIDC_JWKS_UNAVAILABLE",
            "OIDC_JWKS_INVALID",
            "TRUSTED_PROJECTOR_SHA_INVALID",
            "PROJECT_CONCURRENCY_INVALID",
            "PROVIDER_CONFIGURATION_MISSING",
            "INTERNAL_ERROR",
        ):
            with self.subTest(code=code):
                self.assertEqual(
                    self._raise_from_response({"ok": False, "error": code}),
                    f"PROVIDER_OPERATION_FAILED:cursor:{code}",
                )

    def test_unknown_uppercase_code_still_fails_opaque(self):
        self.assertEqual(
            self._raise_from_response({"ok": False, "error": "SYNTHETIC_PRIVATE_MARKER"}),
            "PROVIDER_OPERATION_FAILED:cursor:UNKNOWN",
        )

    def test_non_mapping_response_fails_opaque(self):
        self.assertEqual(
            self._raise_from_response("SYNTHETIC_PRIVATE_MARKER"),
            "PROVIDER_OPERATION_FAILED:cursor:UNKNOWN",
        )

    def test_normal_success_path_unchanged(self):
        client = ProviderProjectionClient(
            "https://synthetic-provider.example.invalid/ingest",
            token_provider=lambda _audience: "synthetic-only",
            transport=lambda _url, _body, *, token_provider: {
                "ok": True,
                "lane": {"identity": "synthetic"},
            },
        )
        self.assertEqual(client.get_lane("synthetic", "main"), {"identity": "synthetic"})


if __name__ == "__main__":
    unittest.main()
