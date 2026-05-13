"""Daemon orchestration: HTTP API, MCP HTTP transport, vault lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
from pathlib import Path
from types import FrameType

import uvicorn

from coral import diag
from coral.config import Config, load_config
from coral.crypto import generate_challenge, generate_token, hash_token
from coral.http_api import HandshakeState, build_http_app
from coral.mcp_server import (
    MCPRuntime,
    build_authed_mcp_http_app,
    build_mcp_server,
    set_runtime,
)
from coral.sessions import SessionServer, recovery_kill_orphan_browsers
from coral.vault import Vault, unlock_vault


async def _provision_cli_token(*, cfg: Config, vault: Vault) -> None:
    raw = generate_token()
    expires_at = int(time.time()) + int(cfg.cli_token_ttl_seconds)
    await vault.insert_token(hash_token(raw), name="cli", expires_at=expires_at)
    path = cfg.cli_token_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


async def run_daemon(*, home: Path | None = None, passphrase: str) -> None:
    """Run Coral until SIGINT/SIGTERM."""
    if home is not None:
        os.environ["CORAL_HOME"] = str(home.resolve())

    cfg = load_config()
    # Orphan-process recovery (spec §7.4): kill Chromium survivors from a
    # crashed previous daemon for *this* coral_home. No-op when nothing matches.
    try:
        orphans_killed = recovery_kill_orphan_browsers(cfg.coral_home)
    except Exception as exc:
        diag.warn("daemon.orphan_sweep_failed", reason=repr(exc))
        orphans_killed = 0
    if orphans_killed:
        diag.warn("daemon.orphan_chromium_killed", count=orphans_killed)

    vault = await unlock_vault(home=cfg.coral_home, passphrase=passphrase)
    await _provision_cli_token(cfg=cfg, vault=vault)
    # Pre-Track-E vaults won't have the bundled behavior packs seeded; idempotent.
    from coral.vault import seed_bundled_behavior_packs

    seeded = await seed_bundled_behavior_packs(vault)
    if seeded:
        diag.info("daemon.seeded_packs", count=seeded)

    challenge = generate_challenge()
    handshake_state = HandshakeState(
        challenge=challenge,
        rate_limit_per_minute=cfg.handshake_rate_limit_per_minute,
    )

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

    if cfg.http_host != "127.0.0.1":
        raise RuntimeError(
            "Coral refuses to bind the HTTP API to anything other than 127.0.0.1 "
            "(spec §6.2 T2). Override is intentionally absent."
        )

    http_app = build_http_app(vault=vault, handshake_state=handshake_state, config=cfg)
    http_server = uvicorn.Server(
        uvicorn.Config(
            http_app,
            host="127.0.0.1",
            port=cfg.http_port,
            log_level="info",
            loop="asyncio",
            timeout_graceful_shutdown=5,
        )
    )

    session_server = SessionServer(
        vault=vault,
        max_duration_minutes=cfg.session_max_duration_minutes,
        coral_home=cfg.coral_home,
    )
    set_runtime(MCPRuntime(vault=vault, agent_name="mcp-http", session_server=session_server))
    mcp = build_mcp_server(http_host="127.0.0.1", http_port=cfg.mcp_http_port)
    mcp_server = uvicorn.Server(
        uvicorn.Config(
            build_authed_mcp_http_app(mcp, vault=vault),
            host="127.0.0.1",
            port=cfg.mcp_http_port,
            log_level="info",
            loop="asyncio",
            timeout_graceful_shutdown=5,
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
        with contextlib.suppress(Exception):
            await session_server.shutdown()
        set_runtime(None)
        await vault.close()
        pid_path.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            cfg.cli_token_path.unlink(missing_ok=True)


def run_daemon_blocking(*, home: Path | None = None, passphrase: str) -> None:
    """Run :func:`run_daemon` from synchronous callers."""
    asyncio.run(run_daemon(home=home, passphrase=passphrase))


def pid_running(pid: int) -> bool:
    """Return True if ``pid`` is alive (uses ``psutil`` for portability)."""
    import psutil

    return psutil.pid_exists(pid)
