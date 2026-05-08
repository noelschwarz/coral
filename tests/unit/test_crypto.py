"""Unit tests for :mod:`coral.crypto`."""

from __future__ import annotations

import re

import pytest

from coral.crypto import (
    CHALLENGE_PATTERN,
    PRODUCTION_PARAMS,
    TEST_PARAMS,
    constant_time_compare,
    derive_key,
    format_sqlcipher_hex_pragma_key,
    generate_challenge,
    generate_salt,
    generate_token,
    hash_token,
)


def test_argon2_deterministic_for_fixed_inputs() -> None:
    salt = b"\x00" * TEST_PARAMS.salt_len
    key1 = derive_key("abcdefghijkl", salt, params=TEST_PARAMS)
    key2 = derive_key("abcdefghijkl", salt, params=TEST_PARAMS)
    assert key1 == key2
    assert isinstance(key1, bytearray)


def test_argon2_differs_across_salts() -> None:
    k1 = derive_key(
        "abcdefghijkl",
        b"\x01" * TEST_PARAMS.salt_len,
        params=TEST_PARAMS,
    )
    k2 = derive_key(
        "abcdefghijkl",
        b"\x02" * TEST_PARAMS.salt_len,
        params=TEST_PARAMS,
    )
    assert bytes(k1) != bytes(k2)


def test_short_passphrase_rejected() -> None:
    with pytest.raises(ValueError, match="12"):
        derive_key("short", b"\x00" * TEST_PARAMS.salt_len, params=TEST_PARAMS)


def test_wrong_salt_length_rejected() -> None:
    with pytest.raises(ValueError, match="Salt"):
        derive_key("abcdefghijkl", b"short", params=TEST_PARAMS)


def test_generate_token_unique_probabilistic() -> None:
    samples = {generate_token() for _ in range(48)}
    assert len(samples) >= 47


def test_hash_token_hex_deterministic() -> None:
    token = generate_token()
    assert hash_token(token) == hash_token(token)
    assert len(hash_token(token)) == 64


def test_challenge_matches_regex() -> None:
    sample = generate_challenge()
    assert re.fullmatch(CHALLENGE_PATTERN, sample) is not None


def test_constant_time_compare() -> None:
    assert constant_time_compare("abc", "abc") is True
    assert constant_time_compare("abc", "abd") is False
    assert constant_time_compare("a", "ab") is False


def test_format_sqlcipher_hex_pragma_key_bounds() -> None:
    key32 = bytearray(32)
    lit = format_sqlcipher_hex_pragma_key(key32)
    assert lit.startswith("x'") and lit.endswith("'")
    with pytest.raises(ValueError):
        format_sqlcipher_hex_pragma_key(b"\x00" * 31)


def test_generate_salt_length() -> None:
    assert len(generate_salt(params=TEST_PARAMS)) == TEST_PARAMS.salt_len


def test_production_params_defaults() -> None:
    assert PRODUCTION_PARAMS.memory_cost == 65536
    assert PRODUCTION_PARAMS.time_cost == 3
    assert PRODUCTION_PARAMS.parallelism == 4
