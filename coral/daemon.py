"""Daemon orchestration: HTTP API, MCP HTTP transport, and lifecycle hooks.

The Coral daemon is a single asyncio process (spec §3.2). Playwright and policy
integration arrive in later milestones; this module only wires networking and vault
unlock validation.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import uvicorn

from coral.config import load_config
from coral.http_api import build_http_app
from coral.mcp_server import build_mcp_server
from coral.paths import daemon_pid_path
from coral.vault import validate_vault_unlock


async def run_daemon(*, home: Path, passphrase: str) -> None:
    """Run the Coral daemon until cancelled or `KeyboardInterrupt`."""
    cfg = load_config(home=home)
    validate_vault_unlock(home=home, passphrase=passphrase)

    pid_path = daemon_pid_path(home)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()), encoding="utf-8")

    http_app = build_http_app()
    mcp = build_mcp_server()
    mcp_app = mcp.streamable_http_app()

    http_server = uvicorn.Server(
        uvicorn.Config(
            http_app,
            host=cfg.http_host,
            port=cfg.http_port,
            log_level="info",
            loop="asyncio",
        )
    )
    mcp_server = uvicorn.Server(
        uvicorn.Config(
            mcp_app,
            host=cfg.mcp_http_host,
            port=cfg.mcp_http_port,
            log_level="info",
            loop="asyncio",
        )
    )

    try:
        await asyncio.gather(http_server.serve(), mcp_server.serve())
    finally:
        pid_path.unlink(missing_ok=True)


def run_daemon_blocking(*, home: Path, passphrase: str) -> None:
    """Run :func:`run_daemon` from synchronous entrypoints (child process)."""
    try:
        asyncio.run(run_daemon(home=home, passphrase=passphrase))
    except KeyboardInterrupt:
        return


def _read_passphrase_from_env_file() -> str:
    raw_path = os.environ.get("CORAL_PASSPHRASE_FILE", "").strip()
    if not raw_path:
        raise RuntimeError("Missing CORAL_PASSPHRASE_FILE environment variable.")
    path = Path(raw_path)
    try:
        return path.read_text(encoding="utf-8")
    finally:
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    # Child process entry (`python -m coral.daemon`).
    from coral.paths import coral_home

    passphrase = _read_passphrase_from_env_file()
    home = coral_home()
    run_daemon_blocking(home=home, passphrase=passphrase)
