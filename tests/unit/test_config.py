"""Unit tests for :mod:`coral.config`."""

from __future__ import annotations

import pytest

from coral.config import ensure_config_file_exists, load_config


def test_load_defaults_when_missing_file(monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> None:
    home = tmp_path_factory.mktemp("cfg")
    monkeypatch.setenv("CORAL_HOME", str(home))
    cfg = load_config()
    assert cfg.coral_home == home
    assert cfg.http_port == 8765


def test_env_overlays_http(monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> None:
    home = tmp_path_factory.mktemp("cfg2")
    monkeypatch.setenv("CORAL_HOME", str(home))
    monkeypatch.setenv("CORAL_HTTP_HOST", "127.0.0.2")
    monkeypatch.setenv("CORAL_HTTP_PORT", "9999")
    monkeypatch.setenv("CORAL_MCP_HTTP_PORT", "9998")
    cfg = load_config()
    assert cfg.http_host == "127.0.0.2"
    assert cfg.http_port == 9999
    assert cfg.mcp_http_port == 9998


def test_invalid_toml_raises(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path_factory.mktemp("badtoml")
    (home / "config.toml").write_text("not[[valid", encoding="utf-8")
    monkeypatch.setenv("CORAL_HOME", str(home))
    with pytest.raises(ValueError, match="Invalid TOML"):
        load_config()


def test_ensure_config_file_created(tmp_path_factory: pytest.TempPathFactory) -> None:
    home = tmp_path_factory.mktemp("ensure")
    path = ensure_config_file_exists(home=home)
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "http_port = 8765" in text
