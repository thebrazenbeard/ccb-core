from __future__ import annotations

import pytest

from chat_bus.digests import is_sha256, require_sha256


VALID = "a" * 64


def test_accepts_exact_lowercase_sha256() -> None:
    assert is_sha256(VALID)
    assert require_sha256(VALID) == VALID


@pytest.mark.parametrize(
    "value",
    [
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "a" + "Z" * 63,
        "g" * 64,
        "-" * 64,
        None,
        123,
    ],
)
def test_rejects_malformed_sha256(value: object) -> None:
    assert not is_sha256(value)
    with pytest.raises(ValueError):
        require_sha256(value)
