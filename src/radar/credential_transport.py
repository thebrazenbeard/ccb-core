"""Fail-closed transport boundaries for bearer-bearing Radar HTTPS requests.

This shared transport is deliberately independent of a specific data provider.
"""

from __future__ import annotations

from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class CredentialTransportError(ValueError):
    pass


GITHUB_ACTIONS_OIDC_HOSTS = frozenset({
    "pipelines.actions.githubusercontent.com",
    "token.actions.githubusercontent.com",
    "vstoken.actions.githubusercontent.com",
})


class _RejectCredentialRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "CREDENTIAL_REDIRECT_FORBIDDEN", headers, fp)


def validate_https_destination(url: str, *, github_oidc: bool = False) -> str:
    """Validate the *original* destination before issuing or requesting a token.

    GitHub runner tokens are allowed only to GitHub-owned Actions OIDC hosts.
    Provider requests remain provider-neutral, but the caller-selected origin is
    fixed because this transport never follows redirects.
    """
    if not isinstance(url, str) or not url or url != url.strip():
        raise CredentialTransportError("CREDENTIAL_URL_INVALID")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise CredentialTransportError("CREDENTIAL_URL_INVALID") from exc
    if (
        parts.scheme.lower() != "https"
        or not host
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
        or any(char.isspace() for char in url)
    ):
        raise CredentialTransportError("CREDENTIAL_HTTPS_DESTINATION_REQUIRED")
    if github_oidc and (host not in GITHUB_ACTIONS_OIDC_HOSTS or port not in (None, 443)):
        raise CredentialTransportError("GITHUB_OIDC_HOST_UNTRUSTED")
    return url


def open_without_redirect(request: Request, *, timeout: int):
    """Use the system TLS stack, but never reissue Authorization on 30x."""
    return build_opener(_RejectCredentialRedirect()).open(request, timeout=timeout)


__all__ = [
    "CredentialTransportError",
    "open_without_redirect",
    "validate_https_destination",
]
