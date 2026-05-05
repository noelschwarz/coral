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


def vault_meta_path(home: Path | None = None) -> Path:
    """Return path to vault key derivation metadata (salt + Argon2 parameters)."""
    base = home or coral_home()
    return base / "vault.meta.json"


def daemon_pid_path(home: Path | None = None) -> Path:
    """Return the daemon PID file path used by ``coral start`` / ``coral stop``."""
    base = home or coral_home()
    return base / "daemon.pid"


def config_path(home: Path | None = None) -> Path:
    """Return the user configuration path (TOML)."""
    base = home or coral_home()
    return base / "config.toml"
