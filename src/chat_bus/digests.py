"""Digest validation helpers shared by the ledger and tests.

This module provides small, well-typed helpers to validate SHA-256
digests expressed as 64-character lowercase hexadecimal strings.
"""

from __future__ import annotations

import re

# TypeGuard is available on Python 3.10+. Fall back to typing_extensions when
# running on older Pythons. Use a runtime import to avoid adding a hard
# dependency for most users.
try:
    from typing import TypeGuard  # type: ignore
except Exception:  # pragma: no cover - fallback for older Python
    from typing_extensions import TypeGuard  # type: ignore

__all__ = ["is_sha256", "require_sha256"]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


def is_sha256(value: object) -> "TypeGuard[str]":
    """Return True only for exactly 64 lowercase hexadecimal characters.

    This is a narrow check: it accepts only str and only lowercase hex digits.
    Use :func:`require_sha256` when you want a validated string or a
    :class:`ValueError` on error.
    """

    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def require_sha256(value: object, *, field: str = "sha256") -> str:
    """Validate and return a SHA-256 hex string, raising ValueError otherwise.

    Args:
        value: Value to validate.
        field: Field name used in the ValueError message.

    Returns:
        The original string (typed as ``str``) when validation succeeds.

    Raises:
        ValueError: if ``value`` is not a 64-character lowercase hex string.
    """
    if not is_sha256(value):
        raise ValueError(f"{field} must be exactly 64 lowercase hexadecimal characters")
    return value
