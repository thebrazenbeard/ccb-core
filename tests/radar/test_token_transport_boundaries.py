"""Synthetic local-network adversarial checks for bearer-bearing transports.

These tests never use real credentials or an external provider.
"""

from __future__ import annotations

from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request
import json
import os
from threading import Thread
import unittest
from unittest.mock import patch

from radar.credential_transport import open_without_redirect
from radar.github_actions_oidc import GithubActionsOidcError, request_github_actions_oidc_token


class _Receiver(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.server.auth_headers.append(self.headers.get("Authorization"))
        if self.server.redirect_to:
            self.send_response(302)
            self.send_header("Location", self.server.redirect_to)
            self.end_headers()
            return
        data = json.dumps({"ok": True, "value": "synthetic-oidc"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        self.do_GET()

    def log_message(self, *_args):
        return


class _SyntheticResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self, max_bytes: int) -> bytes:
        return self.payload[:max_bytes]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class TransportBoundaryTests(unittest.TestCase):
    def setUp(self):
        self._stack = ExitStack()
        self.addCleanup(self._stack.close)
        self.sink = self._server()
        self.origin = self._server(redirect_to=f"http://127.0.0.1:{self.sink.server_port}/stolen")

    def _server(self, redirect_to=None):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
        server.redirect_to = redirect_to
        server.auth_headers = []
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self._stack.callback(worker.join, 2)
        self._stack.callback(server.server_close)
        self._stack.callback(server.shutdown)
        return server

    def test_oidc_request_never_leaks_runner_bearer_to_redirect_target(self):
        env = {
            "ACTIONS_ID_TOKEN_REQUEST_URL": f"http://127.0.0.1:{self.origin.server_port}/token",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "synthetic-runner-secret",
        }
        with patch.dict(os.environ, env):
            with self.assertRaises(GithubActionsOidcError):
                try:
                    request_github_actions_oidc_token("synthetic-audience")
                finally:
                    self.assertEqual(self.sink.auth_headers, [])

    def test_redirect_rejected_by_actual_urllib_handler_after_initial_request(self):
        request = Request(
            f"http://127.0.0.1:{self.origin.server_port}/synthetic",
            headers={"Authorization": "Bearer synthetic-only"},
        )
        with self.assertRaises(HTTPError) as raised:
            open_without_redirect(request, timeout=3)
        self.assertEqual(raised.exception.code, 302)
        self.assertEqual(self.origin.auth_headers, ["Bearer synthetic-only"])
        self.assertEqual(self.sink.auth_headers, [])

    def test_oidc_rejects_foreign_https_host_before_runner_secret_sent(self):
        env = {
            "ACTIONS_ID_TOKEN_REQUEST_URL": "https://attacker.example.invalid/oidc",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "synthetic-runner-secret",
        }
        with patch.dict(os.environ, env):
            with self.assertRaisesRegex(GithubActionsOidcError, "GITHUB_OIDC_HOST_UNTRUSTED"):
                request_github_actions_oidc_token("synthetic-audience")

    def test_oidc_trusted_host_success_and_oversize_fail_closed(self):
        env = {
            "ACTIONS_ID_TOKEN_REQUEST_URL": "https://pipelines.actions.githubusercontent.com/oidc?existing=1",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "synthetic-runner-secret",
        }
        seen_requests = []

        def fake_open(request, *, timeout):
            seen_requests.append((request, timeout))
            return _SyntheticResponse(b'{"value":"synthetic-oidc"}')

        with patch.dict(os.environ, env), patch(
            "radar.github_actions_oidc.open_without_redirect", side_effect=fake_open
        ):
            self.assertEqual(request_github_actions_oidc_token("synthetic-audience"), "synthetic-oidc")
        self.assertIn("audience=synthetic-audience", seen_requests[0][0].full_url)
        self.assertEqual(seen_requests[0][0].get_header("Authorization"), "Bearer synthetic-runner-secret")

        with patch.dict(os.environ, env), patch(
            "radar.github_actions_oidc.open_without_redirect",
            return_value=_SyntheticResponse(b"x" * (64 * 1024 + 1)),
        ):
            with self.assertRaisesRegex(GithubActionsOidcError, "GITHUB_OIDC_RESPONSE_TOO_LARGE"):
                request_github_actions_oidc_token("synthetic-audience")


if __name__ == "__main__":
    unittest.main()
