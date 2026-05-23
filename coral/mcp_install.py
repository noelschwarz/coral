"""Generate MCP client configuration entries for Coral (Track M / PR M1).

Wires Coral into the most-used MCP clients (Claude Desktop, Cursor, Claude
Code) without making the user hand-edit JSON. Writes a server entry that
spawns ``coral mcp-stdio`` on demand.

Threat-model note (relates to T1): this module only writes to the user's
own client-config files under their home directory. It does not launch
the client, does not read or modify Coral's vault, and does not transmit
the user's passphrase or any vault content.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

SERVER_KEY = "mcpServers"
DEFAULT_NAME = "coral"


class MCPInstallError(Exception):
    """Failed to install an MCP server entry."""


@dataclass(frozen=True)
class Client:
    """A known MCP client we can configure."""

    name: str  # CLI key, e.g. "claude-desktop"
    label: str  # human-friendly name for messages
    config_path: Path  # absolute path to the client's JSON config


KNOWN_CLIENTS: tuple[str, ...] = ("claude-desktop", "cursor", "claude-code")


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def _claude_desktop_config_path() -> Path:
    if sys.platform == "darwin":
        return _home() / "Library/Application Support/Claude/claude_desktop_config.json"
    if sys.platform.startswith("linux"):
        # Unofficial Linux builds use ~/.config/Claude/. Anthropic doesn't ship
        # an official Linux desktop yet (as of 2026-05), so this is best-effort.
        return _home() / ".config/Claude/claude_desktop_config.json"
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Claude" / "claude_desktop_config.json"
        raise MCPInstallError("Couldn't resolve %APPDATA% for Claude Desktop config.")
    raise MCPInstallError(f"Claude Desktop config path is not known for platform {sys.platform!r}.")


def _cursor_config_path() -> Path:
    # Cursor uses ~/.cursor/mcp.json on every platform.
    return _home() / ".cursor" / "mcp.json"


def _claude_code_config_path() -> Path:
    # Claude Code stores user-level settings at ~/.claude.json (top-level
    # `mcpServers` key). The `claude mcp add` CLI writes here.
    return _home() / ".claude.json"


def get_client(name: str) -> Client:
    """Resolve a CLI key to a :class:`Client` with the platform-correct path.

    Raises :class:`MCPInstallError` if ``name`` is unknown.
    """
    if name == "claude-desktop":
        return Client(
            name="claude-desktop",
            label="Claude Desktop",
            config_path=_claude_desktop_config_path(),
        )
    if name == "cursor":
        return Client(name="cursor", label="Cursor", config_path=_cursor_config_path())
    if name == "claude-code":
        return Client(
            name="claude-code",
            label="Claude Code",
            config_path=_claude_code_config_path(),
        )
    raise MCPInstallError(f"unknown client {name!r}; known clients: {', '.join(KNOWN_CLIENTS)}")


@dataclass(frozen=True)
class InstallResult:
    client: Client
    config_path: Path
    entry_name: str
    overwrote_existing: bool
    created_config_file: bool


def server_entry(
    *,
    coral_home: Path | None = None,
    command: str = "coral",
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the MCP-server payload that goes into ``mcpServers["coral"]``."""
    entry: dict[str, Any] = {
        "command": command,
        "args": list(args) if args is not None else ["mcp-stdio"],
    }
    extra_env: dict[str, str] = {}
    if env:
        extra_env.update(env)
    if coral_home is not None:
        # Only emit CORAL_HOME if explicitly provided; helps multi-home setups
        # but stays out of the way of users running the default ``~/.coral``.
        extra_env["CORAL_HOME"] = str(coral_home.resolve())
    if extra_env:
        entry["env"] = extra_env
    return entry


def _load_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MCPInstallError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise MCPInstallError(f"{path} root is not a JSON object.")
    return cast(dict[str, Any], loaded)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def install(
    client_name: str,
    *,
    name: str = DEFAULT_NAME,
    coral_home: Path | None = None,
    force: bool = False,
) -> InstallResult:
    """Add a Coral MCP-server entry to ``client_name``'s config.

    Creates the config file (and its parent directory) if it doesn't exist.
    Refuses to overwrite an existing entry with the same ``name`` unless
    ``force=True``.
    """
    client = get_client(client_name)
    created = not client.config_path.is_file()
    config = _load_config(client.config_path)

    servers_raw = config.get(SERVER_KEY)
    if servers_raw is None:
        servers: dict[str, Any] = {}
        config[SERVER_KEY] = servers
    elif isinstance(servers_raw, dict):
        servers = servers_raw  # type: ignore[assignment]
    else:
        raise MCPInstallError(f"{client.config_path}.{SERVER_KEY} exists but is not a JSON object.")

    overwrote = name in servers
    if overwrote and not force:
        raise MCPInstallError(
            f"An MCP server named {name!r} already exists in {client.config_path}. "
            "Use --force to overwrite, or --name to pick a different entry name."
        )

    servers[name] = server_entry(coral_home=coral_home)
    _atomic_write_json(client.config_path, config)

    return InstallResult(
        client=client,
        config_path=client.config_path,
        entry_name=name,
        overwrote_existing=overwrote,
        created_config_file=created,
    )


def uninstall(client_name: str, *, name: str = DEFAULT_NAME) -> bool:
    """Remove the named server entry. Returns True if removed, False if absent.

    Idempotent — never raises just because the entry wasn't there.
    """
    client = get_client(client_name)
    if not client.config_path.is_file():
        return False
    config = _load_config(client.config_path)
    servers = config.get(SERVER_KEY)
    if not isinstance(servers, dict) or name not in servers:
        return False
    del servers[name]
    _atomic_write_json(client.config_path, config)
    return True


def get_entry(client_name: str, *, name: str = DEFAULT_NAME) -> dict[str, Any] | None:
    """Return the current MCP-server entry for ``name``, or None if absent."""
    client = get_client(client_name)
    if not client.config_path.is_file():
        return None
    config = _load_config(client.config_path)
    servers = config.get(SERVER_KEY)
    if not isinstance(servers, dict):
        return None
    entry = cast(dict[str, Any], servers).get(name)
    if isinstance(entry, dict):
        return cast(dict[str, Any], entry)
    return None
