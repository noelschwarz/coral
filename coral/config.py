"""Load daemon and CLI configuration from ``~/.coral/config.toml``."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

from coral.paths import config_path


class CoralConfig(BaseModel):
    """User-facing configuration for Coral.

    This is intentionally minimal for v1 foundations; expand as the product grows.
    """

    http_host: str = Field(default="127.0.0.1", description="Daemon HTTP bind address.")
    http_port: int = Field(default=8765, description="Daemon HTTP port for extension/CLI API.")
    mcp_http_host: str = Field(default="127.0.0.1", description="MCP HTTP bind address.")
    mcp_http_port: int = Field(
        default=8766,
        description="MCP HTTP port (streamable transport).",
    )


def load_config(*, home: Path | None = None) -> CoralConfig:
    """Load config from disk, falling back to defaults when the file does not exist."""
    path = config_path(home)
    if not path.is_file():
        return CoralConfig()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return CoralConfig.model_validate(raw)


def ensure_config_file_exists(*, home: Path | None = None) -> Path:
    """Create ``config.toml`` with defaults if missing; return the path."""
    path = config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        return path
    defaults = CoralConfig().model_dump()
    # Keep deterministic, human-readable defaults for early contributors.
    lines = [
        "# Coral configuration (TOML).",
        "# See coral-engineering-spec.md for semantics.",
        "",
        f'http_host = "{defaults["http_host"]}"',
        f"http_port = {defaults['http_port']}",
        f'mcp_http_host = "{defaults["mcp_http_host"]}"',
        f"mcp_http_port = {defaults['mcp_http_port']}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
