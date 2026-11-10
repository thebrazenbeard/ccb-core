"""GitHub Actions OIDC token acquisition for Radar trusted workflows."""

from __future__ import annotations

import json
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request

from .credential_transport import (
    CredentialTransportError,
    open_without_redirect,
    validate_https_destination,
)


MAX_OIDC_RESPONSE_BYTES = 64 * 1024


class GithubActionsOidcError(RuntimeError):
    pass


def _audience_url(base_url: str, audience: str) -> str:
    parts = urlsplit(base_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["audience"] = audience
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def request_github_actions_oidc_token(audience: str) -> str:
    if not isinstance(audience, str) or not audience:
        raise GithubActionsOidcError("GITHUB_OIDC_AUDIENCE_REQUIRED")
    request_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not request_url or not request_token:
        raise GithubActionsOidcError("GITHUB_OIDC_CONTEXT_MISSING")

    try:
        validate_https_destination(request_url, github_oidc=True)
    except CredentialTransportError as exc:
        raise GithubActionsOidcError(str(exc)) from exc

    request = Request(
        _audience_url(request_url, audience),
        headers={
            "Authorization": f"Bearer {request_token}",
            "Accept": "application/json",
        },
    )
    try:
        with open_without_redirect(request, timeout=20) as response:
            raw = response.read(MAX_OIDC_RESPONSE_BYTES + 1)
    except Exception:
        raise GithubActionsOidcError("GITHUB_OIDC_REQUEST_FAILED") from None
    if len(raw) > MAX_OIDC_RESPONSE_BYTES:
        raise GithubActionsOidcError("GITHUB_OIDC_RESPONSE_TOO_LARGE")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GithubActionsOidcError("GITHUB_OIDC_RESPONSE_INVALID") from exc
    token = body.get("value") if isinstance(body, dict) else None
    if not isinstance(token, str) or not token:
        raise GithubActionsOidcError("GITHUB_OIDC_TOKEN_MISSING")
    return token


__all__ = ["GithubActionsOidcError", "request_github_actions_oidc_token"]
