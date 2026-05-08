"""Filesystem locations for Coral's local-first storage."""

from __future__ import annotations

import os
from pathlib import Path


def coral_home() -> Path:
    """Return the Coral data directory (default ``~/.coral``).

    Override with the ``CORAL_HOME`` environment variable for tests or custom installs.
    """
    raw = os.environ.get("CORAL_HOME", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home() / ".coral"


def vault_db_path(home: Path | None = None) -> Path:
    """Return the encrypted vault database path."""
    base = home or coral_home()
    return base / "vault.db"


def vault_plaintext_meta_path(home: Path | None = None) -> Path:
    """Return path to passphrase derivation metadata (salt + Argon2 parameters).

    This file is intentionally plaintext: the vault encryption key is derived from it
    plus the user's passphrase (ADR-006). Encrypted ``vault_metadata`` mirrors it for
    integrity checks after unlock.
    """
    base = home or coral_home()
    return base / "vault_meta.json"


def daemon_pid_path(home: Path | None = None) -> Path:
    """PID file for ``coral start`` / ``coral stop`` (engineering spec week 1 track)."""
    base = home or coral_home()
    return base / "coral.pid"


def config_path(home: Path | None = None) -> Path:
    """Return the user configuration path (TOML)."""
    base = home or coral_home()
    return base / "config.toml"
