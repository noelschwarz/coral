"""Tests for the OS service installer (Track K).

These tests verify the *file content* and the helper logic. The actual
``launchctl`` / ``systemctl`` calls are not exercised — those require a real
session bus / launchd and would only pass on the right OS, so they're left for
the manual-test path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from coral import service


def test_current_platform_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    assert service.current_platform() is service.Platform.MACOS
    monkeypatch.setattr(sys, "platform", "linux2")
    assert service.current_platform() is service.Platform.LINUX
    monkeypatch.setattr(sys, "platform", "win32")
    assert service.current_platform() is service.Platform.OTHER


def test_install_macos_plist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))  # type: ignore[arg-type]

    result = service.install_service(coral_home=tmp_path / ".coral", passphrase_env=True)
    assert result.label == "dev.coralbridge.daemon"
    assert result.unit_path.is_file()
    body = result.unit_path.read_text()
    assert "<key>Label</key>" in body
    assert "<string>dev.coralbridge.daemon</string>" in body
    assert "RunAtLoad" in body
    assert "ProcessType" in body
    assert "CORAL_HOME" in body
    assert "CORAL_PASSPHRASE" in body  # placeholder present
    # Mode 0600
    mode = result.unit_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_install_macos_plist_no_passphrase_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))  # type: ignore[arg-type]
    result = service.install_service(coral_home=tmp_path / ".coral", passphrase_env=False)
    body = result.unit_path.read_text()
    assert "CORAL_HOME" in body
    assert "CORAL_PASSPHRASE" not in body


def test_install_linux_unit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))  # type: ignore[arg-type]
    result = service.install_service(coral_home=tmp_path / ".coral", passphrase_env=True)
    assert result.label == "coralbridge"
    assert result.unit_path.name == "coralbridge.service"
    body = result.unit_path.read_text()
    assert "[Unit]" in body
    assert "[Service]" in body
    assert "[Install]" in body
    assert "WantedBy=default.target" in body
    assert "Environment=CORAL_HOME=" in body
    assert "Environment=CORAL_PASSPHRASE=" in body  # placeholder


def test_install_other_platform_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(RuntimeError, match="macOS and Linux only"):
        service.install_service(coral_home=Path("/tmp/x"), passphrase_env=False)


def test_uninstall_removes_unit_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))  # type: ignore[arg-type]

    # Stub deactivate_service so we don't actually invoke systemctl.
    monkeypatch.setattr(service, "deactivate_service", lambda: (True, ""))

    service.install_service(coral_home=tmp_path / ".coral", passphrase_env=False)
    unit_path = service.service_paths().unit_path
    assert unit_path.is_file()
    ok, msg = service.uninstall_service()
    assert ok
    assert not unit_path.is_file()
    assert "removed" in msg


def test_xml_escape_is_correct() -> None:
    s = '<dangerous>&"quoted"'
    out = service._xml_escape(s)
    assert "<" not in out.replace("&lt;", "")
    assert ">" not in out.replace("&gt;", "")
    assert "&amp;" in out
    assert "&quot;" in out


def test_systemd_quote_handles_spaces() -> None:
    assert service._systemd_quote("simple") == "simple"
    assert service._systemd_quote("with space") == '"with space"'
    assert service._systemd_quote('with"quote') == '"with\\"quote"'
