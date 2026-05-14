"""In-process CLI tests for the daemon-less paths (Track F cleanup).

The full integration suite exercises the CLI as a subprocess, which doesn't
register in coverage. These tests run Typer's CliRunner against the same
commands and exercise the easy paths so the no-daemon branches don't rot.
"""

from __future__ import annotations

import os
import re

from typer.testing import CliRunner

from coral.cli import app

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    """Rich emits per-span styling (bold/dim) even with NO_COLOR, which splits
    flag names across ANSI sequences. Strip them for substring assertions."""
    return _ANSI_RE.sub("", s)


def test_status_no_daemon(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CORAL_HOME", raising=False)
    result = runner.invoke(app, ["status", "--home", str(tmp_path)])
    assert result.exit_code == 0
    assert "Coral home" in result.stdout
    assert "Vault DB: missing" in result.stdout
    assert "Daemon: not running" in result.stdout


def test_version_flag(monkeypatch) -> None:
    monkeypatch.delenv("CORAL_HOME", raising=False)
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip()  # some version string is printed


def test_audit_without_daemon_exits_nonzero(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CORAL_HOME", raising=False)
    monkeypatch.setenv("CORAL_HOME", str(tmp_path))
    result = runner.invoke(app, ["audit"])
    assert result.exit_code == 1


def test_list_without_daemon_exits_nonzero(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CORAL_HOME", raising=False)
    monkeypatch.setenv("CORAL_HOME", str(tmp_path))
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 1


def test_reviews_without_daemon_exits_nonzero(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CORAL_HOME", raising=False)
    monkeypatch.setenv("CORAL_HOME", str(tmp_path))
    result = runner.invoke(app, ["reviews", "list"])
    assert result.exit_code == 1


def test_help_lists_track_e_commands(monkeypatch) -> None:
    monkeypatch.delenv("CORAL_HOME", raising=False)
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("init", "start", "stop", "status", "audit", "panic", "list", "revoke"):
        assert cmd in result.stdout, f"{cmd} missing from help"


def test_policy_help_subcommand(monkeypatch) -> None:
    monkeypatch.delenv("CORAL_HOME", raising=False)
    result = runner.invoke(app, ["policy", "--help"])
    assert result.exit_code == 0
    assert "get" in result.stdout
    assert "put" in result.stdout


def test_init_force_required_when_vault_exists(tmp_path, monkeypatch) -> None:
    """`coral init` without --force on an existing vault must exit nonzero."""
    monkeypatch.setenv("CORAL_HOME", str(tmp_path))
    monkeypatch.setenv("CORAL_PASSPHRASE", "correct horse battery staple")
    (tmp_path / "vault.db").write_text("placeholder", encoding="utf-8")
    result = runner.invoke(app, ["init", "--home", str(tmp_path)])
    assert result.exit_code != 0
    assert "rotation is not yet supported" in result.stderr or "already exists" in result.stderr


def test_status_handles_corrupt_pid_file(tmp_path, monkeypatch) -> None:
    """A non-numeric PID file shouldn't crash status output."""
    monkeypatch.setenv("CORAL_HOME", str(tmp_path))
    (tmp_path / "coral.pid").write_text("not-a-pid", encoding="utf-8")
    result = runner.invoke(app, ["status", "--home", str(tmp_path)])
    assert result.exit_code == 0
    assert "Daemon" in result.stdout


def test_install_service_help_lists_passphrase_env_flag(monkeypatch) -> None:
    monkeypatch.delenv("CORAL_HOME", raising=False)
    result = runner.invoke(app, ["install-service", "--help"])
    assert result.exit_code == 0
    assert "passphrase-env" in _strip_ansi(result.stdout)


def test_up_help_documents_foreground_and_no_clipboard(monkeypatch) -> None:
    monkeypatch.delenv("CORAL_HOME", raising=False)
    result = runner.invoke(app, ["up", "--help"])
    assert result.exit_code == 0
    stdout = _strip_ansi(result.stdout)
    assert "--foreground" in stdout
    assert "--no-clipboard" in stdout


# Make sure none of these tests leak CORAL_HOME into the rest of the suite.
def teardown_module() -> None:
    os.environ.pop("CORAL_HOME", None)
    os.environ.pop("CORAL_PASSPHRASE", None)
