"""Exact-byte GitHub OIDC transport for Radar provider requests."""

from __future__ import annotations

import hashlib
import json
from typing import Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen


PROVIDER_AUDIENCE_PREFIX = "chat-communication-bus-supabase:"
_MAX_ERROR_BODY_BYTES = 1024
_KNOWN_UNAVAILABLE_CODES = frozenset(
    {
        "OIDC_JWKS_UNAVAILABLE",
        "OIDC_JWKS_INVALID",
        "TRUSTED_PROJECTOR_SHA_INVALID",
        "PROJECT_CONCURRENCY_INVALID",
        "PROVIDER_CONFIGURATION_MISSING",
    }
)


class BodyBoundOidcError(RuntimeError):
    pass


def canonical_json_bytes(payload: object) -> bytes:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BodyBoundOidcError("BOUND_BODY_SERIALIZATION_FAILED") from exc


def body_sha256(body: bytes) -> str:
    if not isinstance(body, bytes):
        raise BodyBoundOidcError("BOUND_BODY_BYTES_REQUIRED")
    return hashlib.sha256(body).hexdigest()


def provider_audience(digest: str) -> str:
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise BodyBoundOidcError("BOUND_BODY_DIGEST_INVALID")
    return f"{PROVIDER_AUDIENCE_PREFIX}{digest}"


def _safe_provider_http_error(exc: HTTPError) -> str:
    """Surface only vetted 503 codes; never echo untrusted provider body text.

    A failed response may contain arbitrary fields or sensitive data. It cannot
    supply an error label unless it matches a code the source itself emits.
    """
    status = exc.code
    label = f"HTTP_{status}"
    if status != 503:
        return label
    try:
        raw = exc.read(_MAX_ERROR_BODY_BYTES + 1)
        if len(raw) > _MAX_ERROR_BODY_BYTES:
            return label
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        # An arbitrary failed response cannot force its body or exception
        # details into the diagnostic path.
        return label
    code = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(code, str) and code in _KNOWN_UNAVAILABLE_CODES:
        return f"{label}:{code}"
    return label


def post_body_bound_json(
    endpoint: str,
    payload: object,
    *,
    token_provider: Callable[[str], str],
    opener: Callable[..., object] = urlopen,
    timeout: int = 45,
) -> dict[str, object]:
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise BodyBoundOidcError("BOUND_ENDPOINT_REQUIRED")
    if type(timeout) is not int or timeout <= 0:
        raise BodyBoundOidcError("BOUND_TIMEOUT_INVALID")

    body = canonical_json_bytes(payload)
    digest = body_sha256(body)
    audience = provider_audience(digest)
    token = token_provider(audience)
    if not isinstance(token, str) or not token:
        raise BodyBoundOidcError("BOUND_OIDC_TOKEN_MISSING")

    request = Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "radar-central-projection-v1",
            "X-Radar-Body-Sha256": digest,
        },
    )
    try:
        with opener(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        raise BodyBoundOidcError(
            f"BOUND_PROVIDER_REQUEST_FAILED:{_safe_provider_http_error(exc)}"
        ) from None
    except Exception as exc:
        raise BodyBoundOidcError(
            f"BOUND_PROVIDER_REQUEST_FAILED:{type(exc).__name__}"
        ) from None

    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BodyBoundOidcError("BOUND_PROVIDER_RESPONSE_INVALID") from exc
    if not isinstance(result, dict):
        raise BodyBoundOidcError("BOUND_PROVIDER_RESPONSE_INVALID")
    return result


__all__ = [
    "BodyBoundOidcError",
    "PROVIDER_AUDIENCE_PREFIX",
    "body_sha256",
    "canonical_json_bytes",
    "post_body_bound_json",
    "provider_audience",
]
