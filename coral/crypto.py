"""Cryptographic helpers: Argon2id key derivation, tokens, handshake challenges."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass

from argon2.low_level import Type, hash_secret_raw

MIN_PASSPHRASE_LENGTH = 12

# Alphanumerics excluding 0, O, 1, I, L for readable terminal challenges.
_CHALLENGE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

CHALLENGE_PATTERN = (
    r"^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$"
)


@dataclass(frozen=True, slots=True)
class Argon2idParams:
    """Argon2id tuning parameters (memory_cost is KiB)."""

    memory_cost: int
    time_cost: int
    parallelism: int
    hash_len: int
    salt_len: int


# Spec §6.3 (~500ms target on reference laptop).
PRODUCTION_PARAMS = Argon2idParams(
    memory_cost=65536,
    time_cost=3,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)

# Fast iteration in tests only — never use for real vaults.
TEST_PARAMS = Argon2idParams(
    memory_cost=8192,
    time_cost=1,
    parallelism=1,
    hash_len=32,
    salt_len=16,
)


def derive_key(passphrase: str, salt: bytes, *, params: Argon2idParams) -> bytearray:
    """Derive a raw SQLCipher key (``hash_len`` bytes) using Argon2id."""
    if len(passphrase) < MIN_PASSPHRASE_LENGTH:
        raise ValueError(
            f"Passphrase must be at least {MIN_PASSPHRASE_LENGTH} characters "
            "(engineering spec §6.2 / T9)."
        )
    if len(salt) != params.salt_len:
        raise ValueError(
            f"Salt must be exactly {params.salt_len} bytes for the selected Argon2idParams."
        )
    raw = hash_secret_raw(
        secret=passphrase.encode("utf-8"),
        salt=salt,
        time_cost=params.time_cost,
        memory_cost=params.memory_cost,
        parallelism=params.parallelism,
        hash_len=params.hash_len,
        type=Type.ID,
    )
    return bytearray(raw)


def format_sqlcipher_hex_pragma_key(raw_key: bytes | bytearray) -> str:
    """Format ``PRAGMA key`` literal for SQLCipher raw hex keys."""
    key_bytes = bytes(raw_key)
    if len(key_bytes) not in {16, 24, 32}:
        raise ValueError("SQLCipher raw key must be 16, 24, or 32 bytes.")
    return "x'" + key_bytes.hex() + "'"


def generate_salt(*, params: Argon2idParams | None = None) -> bytes:
    """Return a new random salt (length from ``params`` or production defaults)."""
    n = params.salt_len if params is not None else PRODUCTION_PARAMS.salt_len
    return secrets.token_bytes(n)


def generate_token() -> str:
    """32 random bytes, base64url without padding."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def hash_token(token: str) -> str:
    """SHA-256 hex digest for ``api_tokens.token_hash``."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_challenge() -> str:
    """Handshake challenge: four groups of four characters (hyphen-separated)."""
    groups = ["".join(secrets.choice(_CHALLENGE_ALPHABET) for _ in range(4)) for _ in range(4)]
    return "-".join(groups)


def constant_time_compare(a: str, b: str) -> bool:
    """Constant-time string comparison for hashed secrets."""
    if len(a) != len(b):
        return False
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
