"""Tests for ``coral mcp install`` (Track M / PR M1).

Uses ``monkeypatch`` to redirect each client's config path into a tmp dir so
we exercise the real read-merge-write logic without touching the user's
actual Claude Desktop / Cursor / Claude Code configs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from coral import mcp_install
from coral.cli import app

runner = CliRunner()


# ---- platform-path resolution ------------------------------------------------


def test_claude_desktop_path_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("HOME", "/Users/test")
    path = mcp_install._claude_desktop_config_path()
    assert path == Path("/Users/test/Library/Application Support/Claude/claude_desktop_config.json")


def test_claude_desktop_path_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("HOME", "/home/test")
    assert mcp_install._claude_desktop_config_path() == Path(
        "/home/test/.config/Claude/claude_desktop_config.json"
    )


def test_cursor_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/test")
    assert mcp_install._cursor_config_path() == Path("/home/test/.cursor/mcp.json")


def test_claude_code_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/test")
    assert mcp_install._claude_code_config_path() == Path("/home/test/.claude.json")


def test_get_client_unknown_raises() -> None:
    with pytest.raises(mcp_install.MCPInstallError, match="unknown client"):
        mcp_install.get_client("not-a-client")


# ---- install: fresh config ---------------------------------------------------


@pytest.fixture
def fake_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Repoint ~ at a tmp directory so all client paths land in tmp."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "darwin")  # deterministic path shape
    return tmp_path


def test_install_creates_fresh_config(fake_home: Path) -> None:
    result = mcp_install.install("cursor")
    assert result.created_config_file is True
    assert result.overwrote_existing is False
    assert result.entry_name == "coral"

    written = json.loads(result.config_path.read_text(encoding="utf-8"))
    assert "mcpServers" in written
    assert written["mcpServers"]["coral"] == {
        "command": "coral",
        "args": ["mcp-stdio"],
    }


def test_install_into_existing_config_merges(fake_home: Path) -> None:
    cfg = mcp_install._cursor_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        json.dumps({"mcpServers": {"other": {"command": "other"}}, "unrelated": 1}),
        encoding="utf-8",
    )

    mcp_install.install("cursor")
    written = json.loads(cfg.read_text(encoding="utf-8"))

    # Pre-existing keys preserved.
    assert written["unrelated"] == 1
    assert written["mcpServers"]["other"] == {"command": "other"}
    # Coral entry added.
    assert written["mcpServers"]["coral"]["command"] == "coral"


def test_install_refuses_overwrite_without_force(fake_home: Path) -> None:
    mcp_install.install("cursor")
    with pytest.raises(mcp_install.MCPInstallError, match="already exists"):
        mcp_install.install("cursor")


def test_install_force_overwrites(fake_home: Path) -> None:
    mcp_install.install("cursor")
    result = mcp_install.install("cursor", coral_home=fake_home / "custom-home", force=True)
    assert result.overwrote_existing is True

    written = json.loads(result.config_path.read_text(encoding="utf-8"))
    assert written["mcpServers"]["coral"]["env"]["CORAL_HOME"].endswith("custom-home")


def test_install_emits_coral_home_when_provided(fake_home: Path) -> None:
    result = mcp_install.install("cursor", coral_home=fake_home / "my-home")
    entry = json.loads(result.config_path.read_text(encoding="utf-8"))["mcpServers"]["coral"]
    assert entry["env"]["CORAL_HOME"].endswith("my-home")


def test_install_omits_env_when_no_coral_home(fake_home: Path) -> None:
    result = mcp_install.install("cursor")
    entry = json.loads(result.config_path.read_text(encoding="utf-8"))["mcpServers"]["coral"]
    assert "env" not in entry


def test_install_named_entry(fake_home: Path) -> None:
    mcp_install.install("cursor", name="coral-work")
    mcp_install.install("cursor", name="coral-personal")
    written = json.loads(mcp_install._cursor_config_path().read_text(encoding="utf-8"))
    assert set(written["mcpServers"].keys()) == {"coral-work", "coral-personal"}


def test_install_rejects_non_object_mcp_servers(fake_home: Path) -> None:
    cfg = mcp_install._cursor_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"mcpServers": ["not an object"]}), encoding="utf-8")
    with pytest.raises(mcp_install.MCPInstallError, match="not a JSON object"):
        mcp_install.install("cursor")


def test_install_rejects_invalid_json(fake_home: Path) -> None:
    cfg = mcp_install._cursor_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("{not json", encoding="utf-8")
    with pytest.raises(mcp_install.MCPInstallError, match="not valid JSON"):
        mcp_install.install("cursor")


# ---- uninstall ---------------------------------------------------------------


def test_uninstall_removes_entry(fake_home: Path) -> None:
    mcp_install.install("cursor")
    assert mcp_install.uninstall("cursor") is True

    written = json.loads(mcp_install._cursor_config_path().read_text(encoding="utf-8"))
    assert "coral" not in written["mcpServers"]


def test_uninstall_is_idempotent_on_missing_file(fake_home: Path) -> None:
    assert mcp_install.uninstall("cursor") is False


def test_uninstall_is_idempotent_on_missing_entry(fake_home: Path) -> None:
    cfg = mcp_install._cursor_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    assert mcp_install.uninstall("cursor") is False


def test_uninstall_leaves_other_entries(fake_home: Path) -> None:
    cfg = mcp_install._cursor_config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "other"}}}), encoding="utf-8")
    mcp_install.install("cursor")
    mcp_install.uninstall("cursor")

    written = json.loads(cfg.read_text(encoding="utf-8"))
    assert written["mcpServers"] == {"other": {"command": "other"}}


# ---- get_entry / status ------------------------------------------------------


def test_get_entry_returns_none_when_absent(fake_home: Path) -> None:
    assert mcp_install.get_entry("cursor") is None


def test_get_entry_returns_payload(fake_home: Path) -> None:
    mcp_install.install("cursor", coral_home=fake_home / "h")
    entry = mcp_install.get_entry("cursor")
    assert entry is not None
    assert entry["command"] == "coral"


# ---- CLI surface -------------------------------------------------------------


def test_cli_install_writes_config(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORAL_HOME", raising=False)
    result = runner.invoke(app, ["mcp", "install", "--client", "cursor"])
    assert result.exit_code == 0, result.output
    assert "Cursor" in result.output
    assert mcp_install._cursor_config_path().is_file()


def test_cli_install_rejects_unknown_client(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CORAL_HOME", raising=False)
    result = runner.invoke(app, ["mcp", "install", "--client", "vim"])
    assert result.exit_code == 2
    assert "Unknown client" in (result.output + (result.stderr or ""))


def test_cli_install_force_overwrites(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORAL_HOME", raising=False)
    runner.invoke(app, ["mcp", "install", "--client", "cursor"])
    result = runner.invoke(app, ["mcp", "install", "--client", "cursor", "--force"])
    assert result.exit_code == 0
    assert "Overwrote" in result.output


def test_cli_status_reports_absent_then_present(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CORAL_HOME", raising=False)

    absent = runner.invoke(app, ["mcp", "status", "--client", "cursor"])
    assert absent.exit_code == 0
    assert "not installed" in absent.output

    runner.invoke(app, ["mcp", "install", "--client", "cursor"])
    present = runner.invoke(app, ["mcp", "status", "--client", "cursor"])
    assert present.exit_code == 0
    assert "mcp-stdio" in present.output


def test_cli_uninstall_idempotent(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORAL_HOME", raising=False)
    result = runner.invoke(app, ["mcp", "uninstall", "--client", "cursor"])
    assert result.exit_code == 0
    assert "No 'coral' entry to remove" in result.output


def test_cli_install_picks_up_coral_home_env(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CORAL_HOME", str(fake_home / "from-env"))
    result = runner.invoke(app, ["mcp", "install", "--client", "cursor"])
    assert result.exit_code == 0
    entry = mcp_install.get_entry("cursor")
    assert entry is not None
    assert "env" in entry
    env_val = entry["env"]  # type: ignore[index]
    assert isinstance(env_val, dict)
    assert env_val["CORAL_HOME"].endswith("from-env")
