"""Provider 503 diagnostics must not echo arbitrary response payloads."""

from io import BytesIO
import traceback
from urllib.error import HTTPError

import pytest

from radar.body_bound_oidc import BodyBoundOidcError, post_body_bound_json


def _failing_opener(status: int, body: bytes):
    def opener(_request, *, timeout: int):
        assert timeout == 45
        raise HTTPError("https://provider.invalid/ingest", status, "failure", {}, BytesIO(body))

    return opener


@pytest.mark.parametrize(
    "code",
    [
        "OIDC_JWKS_UNAVAILABLE",
        "OIDC_JWKS_INVALID",
        "TRUSTED_PROJECTOR_SHA_INVALID",
        "PROJECT_CONCURRENCY_INVALID",
        "PROVIDER_CONFIGURATION_MISSING",
    ],
)
def test_known_503_cause_is_exposed_without_a_provider_body(code: str):
    with pytest.raises(BodyBoundOidcError) as exc_info:
        post_body_bound_json(
            "https://provider.invalid/ingest",
            {"operation": "cursor"},
            token_provider=lambda _audience: "synthetic-test-token",
            opener=_failing_opener(503, ('{"ok":false,"error":"' + code + '"}').encode()),
        )
    assert str(exc_info.value) == f"BOUND_PROVIDER_REQUEST_FAILED:HTTP_503:{code}"


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (503, b'{"ok":false,"error":"SECRET_DO_NOT_REPEAT"}'),
        (503, b'{"error":["TRUSTED_PROJECTOR_SHA_INVALID"]}'),
        (503, b'{"ok":false,"error":"TRUSTED_PROJECTOR_SHA_INVALID","data":"' + b'x' * 1024 + b'"}'),
        (503, b'not-json'),
        (401, b'{"error":"TRUSTED_PROJECTOR_SHA_INVALID"}'),
    ],
)
def test_non_allowlisted_provider_error_body_is_not_exposed(status: int, body: bytes):
    with pytest.raises(BodyBoundOidcError) as exc_info:
        post_body_bound_json(
            "https://provider.invalid/ingest",
            {"operation": "cursor"},
            token_provider=lambda _audience: "synthetic-test-token",
            opener=_failing_opener(status, body),
        )
    assert str(exc_info.value) == f"BOUND_PROVIDER_REQUEST_FAILED:HTTP_{status}"
    assert "SECRET" not in str(exc_info.value)


def test_http_error_traceback_does_not_contain_sensitive_reason():
    def opener(_request, *, timeout: int):
        raise HTTPError(
            "https://provider.invalid/ingest",
            503,
            "SECRET_REASON_DO_NOT_PRINT",
            {},
            BytesIO(b'{"error":"OIDC_JWKS_UNAVAILABLE"}'),
        )

    with pytest.raises(BodyBoundOidcError) as exc_info:
        post_body_bound_json(
            "https://provider.invalid/ingest",
            {},
            token_provider=lambda _audience: "synthetic-test-token",
            opener=opener,
        )
    rendered = "".join(traceback.format_exception(exc_info.value))
    assert "HTTP_503:OIDC_JWKS_UNAVAILABLE" in rendered
    assert "SECRET_REASON_DO_NOT_PRINT" not in rendered


def test_unexpected_transport_exception_does_not_echo_sensitive_message():
    def opener(_request, *, timeout: int):
        raise RuntimeError("SECRET_TRANSPORT_EXCEPTION_DETAIL")

    with pytest.raises(BodyBoundOidcError) as exc_info:
        post_body_bound_json(
            "https://provider.invalid/ingest",
            {},
            token_provider=lambda _audience: "synthetic-test-token",
            opener=opener,
        )
    rendered = "".join(traceback.format_exception(exc_info.value))
    assert "BOUND_PROVIDER_REQUEST_FAILED:RuntimeError" in rendered
    assert "SECRET_TRANSPORT_EXCEPTION_DETAIL" not in rendered
