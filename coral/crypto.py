"""Cryptographic helpers for vault keys and API bearer tokens.

Passphrases, raw keys, and bearer token material must never be logged.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import string
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from argon2.low_level import Type, hash_secret_raw

# Reference parameters from coral-engineering-spec §6.3 (~500ms on a reference laptop).
DEFAULT_ARGON2_TIME_COST: int = 3
DEFAULT_ARGON2_MEMORY_KIB: int = 65536  # 64 MiB
DEFAULT_ARGON2_PARALLELISM: int = 4
DEFAULT_ARGON2_HASH_LEN: int = 32
DEFAULT_ARGON2_TYPE: Type = Type.ID

MIN_PASSPHRASE_LENGTH: int = 12


@dataclass(frozen=True, slots=True)
class Argon2Parameters:
    """Serializable Argon2id parameters used when opening a vault."""

    time_cost: int = DEFAULT_ARGON2_TIME_COST
    memory_kib: int = DEFAULT_ARGON2_MEMORY_KIB
    parallelism: int = DEFAULT_ARGON2_PARALLELISM
    hash_len: int = DEFAULT_ARGON2_HASH_LEN

    def as_dict(self) -> dict[str, int | str]:
        return {
            "argon2_time_cost": self.time_cost,
            "argon2_memory_kib": self.memory_kib,
            "argon2_parallelism": self.parallelism,
            "argon2_hash_len": self.hash_len,
            "argon2_type": "argon2id",
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Argon2Parameters:
        time_cost = int(cast(int | str, data["argon2_time_cost"]))
        memory_kib = int(cast(int | str, data["argon2_memory_kib"]))
        parallelism = int(cast(int | str, data["argon2_parallelism"]))
        hash_len = int(cast(int | str, data["argon2_hash_len"]))
        arg_type = str(data.get("argon2_type", "argon2id"))
        if arg_type != "argon2id":
            raise ValueError(f"Unsupported Argon2 type: {arg_type!r}")
        return cls(
            time_cost=time_cost,
            memory_kib=memory_kib,
            parallelism=parallelism,
            hash_len=hash_len,
        )


def assert_passphrase_policy(passphrase: str) -> None:
    """Validate passphrase strength rules for v1."""
    if len(passphrase) < MIN_PASSPHRASE_LENGTH:
        raise ValueError(
            f"Passphrase must be at least {MIN_PASSPHRASE_LENGTH} characters "
            f"(see engineering spec §6.3 / T9)."
        )


def derive_vault_key(*, passphrase: str, salt: bytes, params: Argon2Parameters) -> bytes:
    """Derive a raw SQLCipher key from a user passphrase using Argon2id."""
    assert_passphrase_policy(passphrase)
    return hash_secret_raw(
        secret=passphrase.encode("utf-8"),
        salt=salt,
        time_cost=params.time_cost,
        memory_cost=params.memory_kib,
        parallelism=params.parallelism,
        hash_len=params.hash_len,
        type=DEFAULT_ARGON2_TYPE,
    )


def format_sqlcipher_hex_pragma_key(raw_key: bytes) -> str:
    """Format a raw key for SQLCipher ``PRAGMA key`` using hex encoding."""
    if len(raw_key) not in {16, 24, 32}:
        raise ValueError("SQLCipher raw key must be 16, 24, or 32 bytes.")
    return "x'" + raw_key.hex() + "'"


def hash_api_token(token: str) -> str:
    """Return the hex-encoded SHA-256 digest of a bearer token for ``api_tokens.token_hash``."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_api_token_bytes() -> bytes:
    """Generate 32 random bytes for an API bearer token (spec §6.3)."""
    return secrets.token_bytes(32)


def encode_api_token(token_bytes: bytes) -> str:
    """Encode API token bytes for transport (URL-safe base64, no padding)."""
    return base64.urlsafe_b64encode(token_bytes).decode("ascii").rstrip("=")


def generate_challenge_code() -> str:
    """Generate a daemon handshake challenge in groups of four alphanumerics.

    Spec §6.3: four groups of four characters from ``secrets`` (~80 bits entropy).
    """
    alphabet = string.ascii_uppercase + string.digits
    groups = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4)]
    return "-".join(groups)


def random_salt(*, num_bytes: int = 16) -> bytes:
    """Generate a new salt for vault key derivation."""
    return secrets.token_bytes(num_bytes)
