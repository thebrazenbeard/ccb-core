"""Exact-byte GitHub OIDC transport for Radar provider requests."""

from __future__ import annotations

import hashlib
import json
from typing import Callable
from urllib.request import Request, urlopen


PROVIDER_AUDIENCE_PREFIX = "chat-communication-bus-supabase:"


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
    except Exception as exc:
        raise BodyBoundOidcError(f"BOUND_PROVIDER_REQUEST_FAILED:{exc}") from exc

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
