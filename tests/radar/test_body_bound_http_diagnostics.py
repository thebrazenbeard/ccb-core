"""Fail-closed, non-secret diagnostics for provider HTTP failures."""

import io
import json
import unittest
from urllib.error import HTTPError

from radar.body_bound_oidc import BodyBoundOidcError, post_body_bound_json


def _raise_http_error(status, body, headers):
    def opener(request, *, timeout):
        raise HTTPError(request.full_url, status, "provider failure", headers, io.BytesIO(body))

    return opener


def _post(opener):
    return post_body_bound_json(
        "https://provider.example.invalid/ingest",
        {"operation": "cursor"},
        token_provider=lambda audience: "opaque-private-token",
        opener=opener,
    )


class ProviderHttpDiagnosticsTests(unittest.TestCase):
    def test_http_503_retains_sanitized_provider_code_and_request_id(self):
        body = json.dumps({"ok": False, "error": "OIDC_JWKS_UNAVAILABLE"}).encode()
        headers = {"sb-request-id": "01a16edb-9d47-7fc0-b709-2751cb14465f"}
        with self.assertRaises(BodyBoundOidcError) as exc:
            _post(_raise_http_error(503, body, headers))
        self.assertEqual(str(exc.exception), (
            "BOUND_PROVIDER_REQUEST_FAILED:HTTP_503:OIDC_JWKS_UNAVAILABLE:"
            "01a16edb-9d47-7fc0-b709-2751cb14465f"
        ))

    def test_http_failure_redacts_arbitrary_provider_body_and_request_id(self):
        secret = "private-token-DO-NOT-EMIT"
        body = json.dumps({"error": secret}).encode()
        headers = {"sb-request-id": secret}
        with self.assertRaises(BodyBoundOidcError) as exc:
            _post(_raise_http_error(503, body, headers))
        self.assertEqual(str(exc.exception), "BOUND_PROVIDER_REQUEST_FAILED:HTTP_503:UNKNOWN:UNKNOWN")
        self.assertNotIn(secret, str(exc.exception))

    def test_http_failure_rejects_oversized_or_non_json_body_without_echoing_it(self):
        secret = b"secret=" + b"x" * 10000
        with self.assertRaises(BodyBoundOidcError) as exc:
            _post(_raise_http_error(502, secret, {}))
        self.assertEqual(str(exc.exception), "BOUND_PROVIDER_REQUEST_FAILED:HTTP_502:UNKNOWN:UNKNOWN")
        self.assertNotIn("secret", str(exc.exception))

    def test_other_transport_exception_does_not_emit_sensitive_details(self):
        def opener(request, *, timeout):
            raise RuntimeError("private-token-DO-NOT-EMIT")

        with self.assertRaises(BodyBoundOidcError) as exc:
            _post(opener)
        self.assertEqual(str(exc.exception), "BOUND_PROVIDER_REQUEST_FAILED:TRANSPORT_ERROR")
