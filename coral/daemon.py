"""Daemon orchestration: HTTP API, MCP HTTP transport, vault lifecycle."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from types import FrameType

import uvicorn

from coral.config import load_config
from coral.crypto import generate_challenge
from coral.http_api import build_http_app
from coral.mcp_server import build_mcp_server
from coral.vault import unlock_vault


async def run_daemon(*, home: Path | None = None, passphrase: str) -> None:
    """Run Coral until SIGINT/SIGTERM."""
    if home is not None:
        os.environ["CORAL_HOME"] = str(home.resolve())

    cfg = load_config()
    vault = await unlock_vault(home=cfg.coral_home, passphrase=passphrase)

    challenge = generate_challenge()
    vault_location = cfg.vault_path
    api_base = f"http://{cfg.http_host}:{cfg.http_port}"

    print(
        "\n".join(
            [
                "Coral daemon started.",
                f"Vault: {vault_location}",
                f"HTTP API: {api_base}",
                "",
                "Extension handshake challenge (paste into the Coral extension popup):",
                "",
                f"    {challenge}",
                "",
                "This challenge is valid for the lifetime of this daemon process.",
                "",
            ]
        ),
        flush=True,
    )

    pid_path = cfg.daemon_pid_file
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()), encoding="utf-8")

    shutdown = asyncio.Event()

    def _request_shutdown() -> None:
        shutdown.set()

    loop = asyncio.get_running_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _request_shutdown)
    except NotImplementedError:

        def _fallback_sig(_signum: int, _frame: FrameType | None) -> None:
            _request_shutdown()

        signal.signal(signal.SIGINT, _fallback_sig)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _fallback_sig)

    http_app = build_http_app()
    http_server = uvicorn.Server(
        uvicorn.Config(
            http_app,
            host=cfg.http_host,
            port=cfg.http_port,
            log_level="info",
            loop="asyncio",
        )
    )
    mcp = build_mcp_server(http_host=cfg.http_host, http_port=cfg.mcp_http_port)
    mcp_server = uvicorn.Server(
        uvicorn.Config(
            mcp.streamable_http_app(),
            host=cfg.http_host,
            port=cfg.mcp_http_port,
            log_level="info",
            loop="asyncio",
        )
    )

    http_task = asyncio.create_task(http_server.serve(), name="coral-http")
    mcp_task = asyncio.create_task(mcp_server.serve(), name="coral-mcp-http")

    try:
        await shutdown.wait()
    finally:
        http_server.should_exit = True
        mcp_server.should_exit = True
        await asyncio.gather(http_task, mcp_task, return_exceptions=True)
        await vault.close()
        pid_path.unlink(missing_ok=True)


def run_daemon_blocking(*, home: Path | None = None, passphrase: str) -> None:
    """Run :func:`run_daemon` from synchronous callers."""
    asyncio.run(run_daemon(home=home, passphrase=passphrase))


def pid_running(pid: int) -> bool:
    """Return True if ``pid`` is alive (uses ``psutil`` for portability)."""
    import psutil

    return psutil.pid_exists(pid)
