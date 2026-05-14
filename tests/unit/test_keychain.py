"""Tests for the OS keychain bridge (Track L / ADR-017).

Real `security` and `secret-tool` calls aren't exercised — they'd require a
real Keychain/libsecret session and would only pass on the right OS. We mock
``subprocess.run`` and ``shutil.which`` so the wrapper logic is testable on any
platform.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from coral import keychain


@pytest.fixture
def coral_home(tmp_path: Path) -> Path:
    return tmp_path / ".coral"


def _fake_completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# ---- platform detection -----------------------------------------------------


def test_is_available_macos_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(keychain.shutil, "which", lambda name: "/usr/bin/security")
    assert keychain.is_available() is True


def test_is_available_macos_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(keychain.shutil, "which", lambda name: None)
    assert keychain.is_available() is False


def test_is_available_linux_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        keychain.shutil,
        "which",
        lambda name: "/usr/bin/secret-tool" if name == "secret-tool" else None,
    )
    assert keychain.is_available() is True


def test_is_available_linux_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(keychain.shutil, "which", lambda name: None)
    assert keychain.is_available() is False


def test_is_available_windows_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    assert keychain.is_available() is False


# ---- store / retrieve / delete (macOS) --------------------------------------


def test_macos_store_calls_security_with_update_flag(
    monkeypatch: pytest.MonkeyPatch, coral_home: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(keychain.shutil, "which", lambda name: "/usr/bin/security")

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return _fake_completed(returncode=0)

    monkeypatch.setattr(keychain.subprocess, "run", fake_run)
    keychain.store(coral_home, "correct horse battery staple")

    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0:2] == ["security", "add-generic-password"]
    assert "-U" in cmd  # update-if-exists
    assert "-s" in cmd and "coralbridge" in cmd
    assert "correct horse battery staple" in cmd


def test_macos_retrieve_returns_stripped_stdout(
    monkeypatch: pytest.MonkeyPatch, coral_home: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(keychain.shutil, "which", lambda name: "/usr/bin/security")
    monkeypatch.setattr(
        keychain.subprocess,
        "run",
        lambda *a, **kw: _fake_completed(returncode=0, stdout="correct horse battery staple\n"),
    )
    assert keychain.retrieve(coral_home) == "correct horse battery staple"


def test_macos_retrieve_raises_not_found_on_exit_44(
    monkeypatch: pytest.MonkeyPatch, coral_home: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(keychain.shutil, "which", lambda name: "/usr/bin/security")
    monkeypatch.setattr(
        keychain.subprocess,
        "run",
        lambda *a, **kw: _fake_completed(
            returncode=44, stderr="The specified item could not be found in the keychain."
        ),
    )
    with pytest.raises(keychain.KeychainNotFound):
        keychain.retrieve(coral_home)


def test_macos_delete_returns_false_when_absent(
    monkeypatch: pytest.MonkeyPatch, coral_home: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(keychain.shutil, "which", lambda name: "/usr/bin/security")
    monkeypatch.setattr(
        keychain.subprocess,
        "run",
        lambda *a, **kw: _fake_completed(returncode=44, stderr="could not be found"),
    )
    assert keychain.delete(coral_home) is False


def test_macos_delete_returns_true_on_success(
    monkeypatch: pytest.MonkeyPatch, coral_home: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(keychain.shutil, "which", lambda name: "/usr/bin/security")
    monkeypatch.setattr(keychain.subprocess, "run", lambda *a, **kw: _fake_completed(returncode=0))
    assert keychain.delete(coral_home) is True


# ---- store / retrieve / delete (Linux) --------------------------------------


def test_linux_store_pipes_passphrase_via_stdin(
    monkeypatch: pytest.MonkeyPatch, coral_home: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(keychain.shutil, "which", lambda name: "/usr/bin/secret-tool")

    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["input"] = kw.get("input")
        return _fake_completed(returncode=0)

    monkeypatch.setattr(keychain.subprocess, "run", fake_run)
    keychain.store(coral_home, "correct horse battery staple")

    assert captured["cmd"][0:2] == ["secret-tool", "store"]
    assert "service" in captured["cmd"]
    assert "coralbridge" in captured["cmd"]
    assert captured["input"] == "correct horse battery staple"


def test_linux_retrieve_returns_not_found_on_empty_stdout(
    monkeypatch: pytest.MonkeyPatch, coral_home: Path
) -> None:
    """secret-tool exits 0 with empty stdout when the item is missing."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(keychain.shutil, "which", lambda name: "/usr/bin/secret-tool")
    monkeypatch.setattr(
        keychain.subprocess, "run", lambda *a, **kw: _fake_completed(returncode=0, stdout="")
    )
    with pytest.raises(keychain.KeychainNotFound):
        keychain.retrieve(coral_home)


def test_linux_retrieve_strips_trailing_newline(
    monkeypatch: pytest.MonkeyPatch, coral_home: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(keychain.shutil, "which", lambda name: "/usr/bin/secret-tool")
    monkeypatch.setattr(
        keychain.subprocess,
        "run",
        lambda *a, **kw: _fake_completed(returncode=0, stdout="hunter2\n"),
    )
    assert keychain.retrieve(coral_home) == "hunter2"


def test_linux_delete_returns_false_when_absent(
    monkeypatch: pytest.MonkeyPatch, coral_home: Path
) -> None:
    """delete() does a lookup first so it can report whether anything was removed."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(keychain.shutil, "which", lambda name: "/usr/bin/secret-tool")
    monkeypatch.setattr(
        keychain.subprocess,
        "run",
        lambda *a, **kw: _fake_completed(returncode=0, stdout=""),
    )
    assert keychain.delete(coral_home) is False


# ---- error surface ----------------------------------------------------------


def test_store_raises_unavailable_when_backend_missing(
    monkeypatch: pytest.MonkeyPatch, coral_home: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(keychain.shutil, "which", lambda name: None)
    with pytest.raises(keychain.KeychainUnavailable):
        keychain.store(coral_home, "pw")


def test_macos_store_propagates_failure(monkeypatch: pytest.MonkeyPatch, coral_home: Path) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(keychain.shutil, "which", lambda name: "/usr/bin/security")
    monkeypatch.setattr(
        keychain.subprocess,
        "run",
        lambda *a, **kw: _fake_completed(returncode=1, stderr="something exploded"),
    )
    with pytest.raises(keychain.KeychainError, match="something exploded"):
        keychain.store(coral_home, "pw")


def test_account_uses_resolved_coral_home(tmp_path: Path) -> None:
    """Different CORAL_HOMEs must produce different account names."""
    a = keychain._account(tmp_path / "a")
    b = keychain._account(tmp_path / "b")
    assert a != b
    assert "vault-passphrase:" in a
