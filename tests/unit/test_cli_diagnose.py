"""In-process tests for the ``coral diagnose`` CLI (Track I)."""

from __future__ import annotations

import os

from typer.testing import CliRunner

from coral.cli import app

runner = CliRunner()


def _strip_ansi(s: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def test_diagnose_runs_against_empty_home(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CORAL_HOME", raising=False)
    monkeypatch.delenv("CORAL_PASSPHRASE", raising=False)
    monkeypatch.delenv("CORAL_HTTP_HOST", raising=False)
    result = runner.invoke(app, ["diagnose", "--home", str(tmp_path)])
    assert result.exit_code == 0
    out = _strip_ansi(result.stdout)
    assert "coralbridge" in out
    assert "Coral home" in out
    assert "Daemon: not running" in out
    # No fail markers on a clean home
    assert "✗" not in out


def test_diagnose_warns_on_passphrase_env(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CORAL_HOME", raising=False)
    monkeypatch.setenv("CORAL_PASSPHRASE", "correct horse battery staple")
    result = runner.invoke(app, ["diagnose", "--home", str(tmp_path)])
    assert result.exit_code == 0
    out = _strip_ansi(result.stdout)
    assert "CORAL_PASSPHRASE is set" in out
    monkeypatch.delenv("CORAL_PASSPHRASE", raising=False)


def test_diagnose_fails_loud_on_non_loopback_http_host(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CORAL_HOME", raising=False)
    monkeypatch.setenv("CORAL_HTTP_HOST", "0.0.0.0")
    result = runner.invoke(app, ["diagnose", "--home", str(tmp_path)])
    assert result.exit_code == 0
    out = _strip_ansi(result.stdout)
    assert "CORAL_HTTP_HOST != 127.0.0.1" in out
    assert "✗" in out  # the failure marker
    monkeypatch.delenv("CORAL_HTTP_HOST", raising=False)


def test_diagnose_flags_world_readable_vault(tmp_path, monkeypatch) -> None:
    """If vault.db is world-readable, diagnose must flag it loudly."""
    monkeypatch.delenv("CORAL_HOME", raising=False)
    vault_path = tmp_path / "vault.db"
    vault_path.write_text("not actually a vault", encoding="utf-8")
    os.chmod(vault_path, 0o644)
    result = runner.invoke(app, ["diagnose", "--home", str(tmp_path)])
    assert result.exit_code == 0
    out = _strip_ansi(result.stdout)
    assert "vault.db" in out
    assert "644" in out
    # Should be a hard failure marker, not just a warning.
    assert "✗" in out


def test_diagnose_quiet_on_correct_modes(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CORAL_HOME", raising=False)
    vault_path = tmp_path / "vault.db"
    vault_path.write_text("not actually a vault", encoding="utf-8")
    os.chmod(vault_path, 0o600)
    cli_token = tmp_path / "cli.token"
    cli_token.write_text("not actually a token", encoding="utf-8")
    os.chmod(cli_token, 0o600)
    result = runner.invoke(app, ["diagnose", "--home", str(tmp_path)])
    assert result.exit_code == 0
    out = _strip_ansi(result.stdout)
    assert "✗" not in out, out


# Don't leak env-var fixtures into the rest of the suite.
def teardown_module() -> None:
    for v in ("CORAL_HOME", "CORAL_PASSPHRASE", "CORAL_HTTP_HOST"):
        os.environ.pop(v, None)
