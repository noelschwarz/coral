"""Encrypted vault management (SQLCipher + SQLAlchemy schema).

This module creates and opens the vault at ``<CORAL_HOME>/vault.db``. Passphrase
material must never be logged.
"""

from __future__ import annotations

import json
from base64 import b64decode, b64encode
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import sqlcipher3.dbapi2 as sqlcipher
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from coral.crypto import (
    Argon2Parameters,
    derive_vault_key,
    format_sqlcipher_hex_pragma_key,
    random_salt,
)
from coral.models import Base
from coral.paths import vault_db_path, vault_meta_path


class VaultError(RuntimeError):
    """Raised when the vault cannot be created or opened."""


@dataclass(frozen=True, slots=True)
class VaultMeta:
    """On-disk metadata required to derive the vault encryption key."""

    salt: bytes
    params: Argon2Parameters

    def to_json_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"salt_b64": b64encode(self.salt).decode("ascii")}
        payload.update(self.params.as_dict())
        return payload

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> VaultMeta:
        salt_b64 = str(data["salt_b64"])
        salt = b64decode(salt_b64.encode("ascii"))
        params_payload = {k: v for k, v in data.items() if k != "salt_b64"}
        params = Argon2Parameters.from_dict(params_payload)
        return cls(salt=salt, params=params)


def write_vault_meta(*, home: Path, meta: VaultMeta) -> Path:
    """Write ``vault.meta.json`` into the Coral home directory."""
    path = vault_meta_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(meta.to_json_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def read_vault_meta(*, home: Path) -> VaultMeta:
    """Read vault derivation metadata."""
    path = vault_meta_path(home)
    if not path.is_file():
        raise VaultError(f"Missing vault metadata file: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise VaultError(f"Invalid vault metadata JSON object at {path}.")
    payload = cast(dict[str, Any], raw)
    return VaultMeta.from_json_dict(payload)


def _open_sql_cipher_connection(db_path: Path, *, raw_key: bytes) -> sqlcipher.Connection:
    conn = sqlcipher.connect(str(db_path))
    key_literal = format_sqlcipher_hex_pragma_key(raw_key)
    conn.execute(f'PRAGMA key = "{key_literal}"')
    return conn


def _make_engine(*, db_path: Path, raw_key: bytes) -> Engine:
    """Create a SQLAlchemy engine backed by SQLCipher."""

    def creator() -> sqlcipher.Connection:
        return _open_sql_cipher_connection(db_path, raw_key=raw_key)

    # SQLAlchemy requires a URL even when using a custom creator.
    return create_engine("sqlite://", creator=creator, future=True)


def init_vault(*, home: Path, passphrase: str) -> Path:
    """Create a new encrypted vault with schema tables and indexes."""
    meta_path = vault_meta_path(home)
    db_path = vault_db_path(home)
    if meta_path.exists() or db_path.exists():
        raise VaultError(
            "A vault already exists in this Coral home directory. "
            f"Refusing to re-initialize ({db_path})."
        )

    params = Argon2Parameters()
    salt = random_salt()
    meta = VaultMeta(salt=salt, params=params)
    raw_key = derive_vault_key(passphrase=passphrase, salt=salt, params=params)

    write_vault_meta(home=home, meta=meta)

    # Ensure parent directory exists for the DB file.
    db_path.parent.mkdir(parents=True, exist_ok=True)

    engine = _make_engine(db_path=db_path, raw_key=raw_key)
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()

    validate_vault_unlock(home=home, passphrase=passphrase)
    return db_path


def validate_vault_unlock(*, home: Path, passphrase: str) -> None:
    """Verify that the passphrase unlocks the vault and the schema is reachable."""
    meta = read_vault_meta(home=home)
    raw_key = derive_vault_key(passphrase=passphrase, salt=meta.salt, params=meta.params)
    db_path = vault_db_path(home)
    if not db_path.is_file():
        raise VaultError(f"Vault database does not exist: {db_path}")

    engine = _make_engine(db_path=db_path, raw_key=raw_key)
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    except Exception as exc:  # pragma: no cover - driver-specific errors vary
        raise VaultError(
            "Failed to unlock vault (incorrect passphrase or corrupt database).",
        ) from exc
    finally:
        engine.dispose()


def table_counts(*, home: Path, passphrase: str) -> dict[str, int]:
    """Return row counts for core tables (debug/diagnostics helper for early milestones)."""
    meta = read_vault_meta(home=home)
    raw_key = derive_vault_key(passphrase=passphrase, salt=meta.salt, params=meta.params)
    db_path = vault_db_path(home)
    engine = _make_engine(db_path=db_path, raw_key=raw_key)
    try:
        with engine.connect() as conn:
            counts: dict[str, int] = {}
            for table in ("sessions", "policies", "audit_log", "api_tokens"):
                row = conn.execute(text(f"select count(*) from {table}")).one()
                counts[table] = int(row[0])
            return counts
    finally:
        engine.dispose()
