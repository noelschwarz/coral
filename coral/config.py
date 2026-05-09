"""Daemon and CLI configuration (``config.toml`` + environment overlays)."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field, computed_field

from coral.paths import coral_home


class Config(BaseModel):
    """Validated Coral configuration (week 1 track defaults)."""

    coral_home: Path = Field(default_factory=coral_home)
    http_host: str = "127.0.0.1"
    http_port: int = 8765
    mcp_http_port: int = 8766
    audit_log_max_age_days: int = 365
    session_max_duration_minutes: int = 60
    extension_token_ttl_seconds: int = 24 * 60 * 60
    cli_token_ttl_seconds: int = 30 * 24 * 60 * 60
    handshake_rate_limit_per_minute: int = 5

    @computed_field  # type: ignore[prop-decorator]
    @property
    def daemon_pid_file(self) -> Path:
        return self.coral_home / "coral.pid"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def vault_path(self) -> Path:
        return self.coral_home / "vault.db"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cli_token_path(self) -> Path:
        return self.coral_home / "cli.token"

    @classmethod
    def load(cls) -> Config:
        """Load ``<coral_home>/config.toml`` when present and overlay env overrides."""
        home = coral_home()
        path = home / "config.toml"
        data: dict[str, object] = {}
        if path.is_file():
            try:
                loaded_any = tomllib.loads(path.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as exc:
                raise ValueError(f"Invalid TOML in configuration file ({path}).") from exc
            data = cast(dict[str, object], loaded_any)

        cfg = cls.model_validate({**data, "coral_home": home})

        updates: dict[str, object] = {}
        if raw_host := os.environ.get("CORAL_HTTP_HOST", "").strip():
            updates["http_host"] = raw_host
        if raw_port := os.environ.get("CORAL_HTTP_PORT", "").strip():
            updates["http_port"] = int(raw_port)
        if raw_mcp := os.environ.get("CORAL_MCP_HTTP_PORT", "").strip():
            updates["mcp_http_port"] = int(raw_mcp)
        if updates:
            return cfg.model_copy(update=updates)
        return cfg


def load_config() -> Config:
    """:func:`Config.load` alias retained for call-site ergonomics."""
    return Config.load()


def ensure_config_file_exists(*, home: Path | None = None) -> Path:
    """Create ``config.toml`` with defaults if missing."""
    base = home.expanduser().resolve() if home is not None else coral_home()
    path = base / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        return path
    template = Config(coral_home=base)
    lines = [
        "# Coral configuration (TOML).",
        "# See coral-engineering-spec.md for semantics.",
        "",
        f'http_host = "{template.http_host}"',
        f"http_port = {template.http_port}",
        f"mcp_http_port = {template.mcp_http_port}",
        f"audit_log_max_age_days = {template.audit_log_max_age_days}",
        f"session_max_duration_minutes = {template.session_max_duration_minutes}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
