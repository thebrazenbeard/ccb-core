"""The provider error transport retains machine codes, not response prose."""
from io import BytesIO
from urllib.error import HTTPError
import pytest
from radar.body_bound_oidc import BodyBoundOidcError, post_body_bound_json

def simulated_http_error(body: bytes):
    def opener(request, timeout):
        raise HTTPError(request.full_url, 503, "Unavailable", {}, BytesIO(body))
    return opener

@pytest.mark.parametrize("reason", (
    "TRUSTED_PROJECTOR_SHA_INVALID",
    "OIDC_JWKS_UNAVAILABLE",
    "PROJECT_CONCURRENCY_INVALID",
    "PROVIDER_CONFIGURATION_MISSING",
))
def test_valid_provider_error_codes_are_preserved(reason):
    with pytest.raises(BodyBoundOidcError) as error:
        post_body_bound_json("https://example.invalid/ingest", {"operation": "cursor"},
            token_provider=lambda _: "opaque-test-value",
            opener=simulated_http_error(('{"ok":false,"error":"' + reason + '"}').encode()))
    assert str(error.value) == "BOUND_PROVIDER_REJECTED:HTTP_503:" + reason

@pytest.mark.parametrize("body", (
    b"non-json",
    b'{"ok":false,"error":"bad diagnostic"}',
    b'{"ok":true,"error":"OIDC_JWKS_UNAVAILABLE"}',
))
def test_response_body_is_never_logged(body):
    with pytest.raises(BodyBoundOidcError) as error:
        post_body_bound_json("https://example.invalid/ingest", {"operation": "cursor"},
            token_provider=lambda _: "opaque-test-value", opener=simulated_http_error(body))
    assert str(error.value) == "BOUND_PROVIDER_REJECTED:HTTP_503"

def test_transport_failure_does_not_expose_exception_message():
    def opener(request, timeout):
        raise ConnectionError("backend details")
    with pytest.raises(BodyBoundOidcError) as error:
        post_body_bound_json("https://example.invalid/ingest", {"operation": "cursor"},
            token_provider=lambda _: "opaque-test-value", opener=opener)
    assert str(error.value) == "BOUND_PROVIDER_REQUEST_FAILED:ConnectionError"
