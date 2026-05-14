"""OS keychain bridge for the daemon's vault passphrase (ADR-017).

Backends:
- macOS: ``security`` CLI (Keychain Access). Always present.
- Linux: ``secret-tool`` CLI (libsecret, GNOME Keyring / KWallet). Usually
  present on desktop Linux; absent on servers and minimal installs.

Entries are keyed by service ``coralbridge`` and a per-CORAL_HOME account, so
multiple Coral installs on one user account don't collide.

Windows: not supported in this track; mirrors ``coral install-service`` scope.

Notes
-----
The passphrase travels through ``argv`` on macOS (`security -w PASS`). That's
visible briefly to ``ps`` for the same uid, which is already inside Coral's
trust boundary (T1). The Linux path uses stdin, no argv leak.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

SERVICE_NAME = "coralbridge"

# `security` exit codes (from /usr/include/Security/SecBase.h via macOS man page).
_SEC_ERR_ITEM_NOT_FOUND = 44


class KeychainError(Exception):
    """Base class for keychain failures."""


class KeychainUnavailable(KeychainError):
    """The OS keychain CLI is not installed or this platform isn't supported."""


class KeychainNotFound(KeychainError):
    """No passphrase entry stored for this CORAL_HOME."""


def _account(coral_home: Path) -> str:
    return f"vault-passphrase:{coral_home.resolve()}"


def is_available() -> bool:
    """True if the backend CLI is on PATH and the platform is supported."""
    if sys.platform == "darwin":
        return shutil.which("security") is not None
    if sys.platform.startswith("linux"):
        return shutil.which("secret-tool") is not None
    return False


def _require_available() -> None:
    if is_available():
        return
    if sys.platform == "darwin":
        raise KeychainUnavailable("macOS `security` CLI not found on PATH.")
    if sys.platform.startswith("linux"):
        raise KeychainUnavailable(
            "Linux `secret-tool` not found on PATH. Install `libsecret-tools` "
            "(Debian/Ubuntu) or `libsecret` (Fedora/Arch) to enable OS keychain "
            "storage, or use `coral install-service --passphrase-env`."
        )
    raise KeychainUnavailable(f"Coral keychain integration is not supported on {sys.platform}.")


def store(coral_home: Path, passphrase: str) -> None:
    """Stash the passphrase in the OS keychain. Overwrites any existing entry."""
    _require_available()
    account = _account(coral_home)
    if sys.platform == "darwin":
        r = subprocess.run(
            [
                "security",
                "add-generic-password",
                "-U",  # update if exists
                "-s",
                SERVICE_NAME,
                "-a",
                account,
                "-w",
                passphrase,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode != 0:
            raise KeychainError(
                f"`security add-generic-password` failed: {r.stderr.strip() or r.stdout.strip()}"
            )
        return
    if sys.platform.startswith("linux"):
        r = subprocess.run(
            [
                "secret-tool",
                "store",
                "--label=Coral vault passphrase",
                "service",
                SERVICE_NAME,
                "account",
                account,
            ],
            input=passphrase,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode != 0:
            raise KeychainError(
                f"`secret-tool store` failed: {r.stderr.strip() or r.stdout.strip()}"
            )
        return
    raise KeychainUnavailable(f"unsupported platform: {sys.platform}")


def retrieve(coral_home: Path) -> str:
    """Return the stored passphrase. Raises ``KeychainNotFound`` if absent."""
    _require_available()
    account = _account(coral_home)
    if sys.platform == "darwin":
        r = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                SERVICE_NAME,
                "-a",
                account,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode == _SEC_ERR_ITEM_NOT_FOUND or "could not be found" in r.stderr:
            raise KeychainNotFound(account)
        if r.returncode != 0:
            raise KeychainError(f"`security find-generic-password` failed: {r.stderr.strip()}")
        return r.stdout.rstrip("\n")
    if sys.platform.startswith("linux"):
        r = subprocess.run(
            [
                "secret-tool",
                "lookup",
                "service",
                SERVICE_NAME,
                "account",
                account,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # secret-tool exits 0 with empty stdout when the item is missing.
        if r.returncode == 0 and not r.stdout:
            raise KeychainNotFound(account)
        if r.returncode != 0:
            raise KeychainError(f"`secret-tool lookup` failed: {r.stderr.strip()}")
        return r.stdout.rstrip("\n")
    raise KeychainUnavailable(f"unsupported platform: {sys.platform}")


def delete(coral_home: Path) -> bool:
    """Remove the passphrase entry. Returns True if one was removed, False if
    nothing was stored. Idempotent."""
    _require_available()
    account = _account(coral_home)
    if sys.platform == "darwin":
        r = subprocess.run(
            [
                "security",
                "delete-generic-password",
                "-s",
                SERVICE_NAME,
                "-a",
                account,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode == 0:
            return True
        if r.returncode == _SEC_ERR_ITEM_NOT_FOUND or "could not be found" in r.stderr:
            return False
        raise KeychainError(f"`security delete-generic-password` failed: {r.stderr.strip()}")
    if sys.platform.startswith("linux"):
        # `secret-tool clear` is silent whether or not anything matched; do a
        # lookup first so we can return a meaningful boolean.
        try:
            retrieve(coral_home)
        except KeychainNotFound:
            return False
        r = subprocess.run(
            [
                "secret-tool",
                "clear",
                "service",
                SERVICE_NAME,
                "account",
                account,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode != 0:
            raise KeychainError(f"`secret-tool clear` failed: {r.stderr.strip()}")
        return True
    raise KeychainUnavailable(f"unsupported platform: {sys.platform}")
