"""Typer CLI entrypoint (spec §3.1 CLI + §7).

Commands are introduced incrementally across the 4-week plan. This module owns user
interaction boundaries like passphrase prompts (never log passphrases).
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from contextlib import suppress
from pathlib import Path

import psutil
import typer

from coral import __version__
from coral.config import ensure_config_file_exists, load_config
from coral.crypto import MIN_PASSPHRASE_LENGTH
from coral.daemon import pid_running, run_daemon_blocking
from coral.paths import coral_home, vault_db_path, vault_plaintext_meta_path
from coral.vault import (
    Vault,
    VaultError,
    VaultIntegrityError,
    VaultLockedError,
)

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Coral: local-first browser session bridge (daemon + CLI + Chrome extension).",
)


@app.callback(invoke_without_command=True)
def _main_callback(  # pyright: ignore[reportUnusedFunction]
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        help="Print Coral's version and exit.",
        is_eager=True,
    ),
) -> None:
    """Coral root group."""
    if version:
        typer.echo(__version__)
        raise typer.Exit(code=0)
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(code=0)


def _home(explicit: Path | None) -> Path:
    return explicit.expanduser().resolve() if explicit is not None else coral_home()


def _new_passphrase_prompt() -> str:
    env = os.environ.get("CORAL_PASSPHRASE", "").strip()
    if env:
        return env
    first = typer.prompt("New vault passphrase", hide_input=True)
    second = typer.prompt("Confirm passphrase", hide_input=True)
    if first != second:
        typer.secho("Passphrases do not match; aborting.", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=2)
    return first


def _unlock_passphrase_prompt() -> str:
    env = os.environ.get("CORAL_PASSPHRASE", "").strip()
    if env:
        return env
    return typer.prompt("Vault passphrase", hide_input=True)


@app.command()
def init(
    home: Path | None = typer.Option(
        None,
        "--home",
        help="Coral data directory (default: ~/.coral or $CORAL_HOME).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Destroy any existing vault in this Coral home after interactive confirmation.",
    ),
) -> None:
    """Create a new encrypted vault at ~/.coral/vault.db (configurable via --home / $CORAL_HOME)."""
    coral_dir = _home(home)
    os.environ["CORAL_HOME"] = str(coral_dir)

    db_path = vault_db_path(coral_dir)
    meta_path = vault_plaintext_meta_path(coral_dir)
    vault_exists = db_path.is_file() or meta_path.is_file()

    if vault_exists and not force:
        typer.secho(
            f"Vault already exists at {db_path}. "
            "Use `coral init --force` to wipe and reinitialize, "
            "or delete the files manually. Note: rotation is not yet supported.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    if vault_exists and force:
        typer.confirm(
            f"This will permanently delete the existing Coral vault in {coral_dir}. Continue?",
            abort=True,
        )
        db_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)

    passphrase = _new_passphrase_prompt()

    if len(passphrase) < MIN_PASSPHRASE_LENGTH:
        typer.secho(
            f"Passphrase must be at least {MIN_PASSPHRASE_LENGTH} characters "
            "(engineering spec §6.2 / T9).",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2)

    async def _run_init() -> None:
        ensure_config_file_exists(home=coral_dir)
        vault = await Vault.initialize(coral_dir, passphrase)
        await vault.close()

    try:
        asyncio.run(_run_init())
    except VaultError as exc:
        typer.secho(f"init failed: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Initialized encrypted vault at {db_path}")


@app.command()
def start(
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
) -> None:
    """Start the Coral daemon (HTTP + MCP HTTP). Runs until SIGINT or SIGTERM."""
    coral_dir = _home(home)
    os.environ["CORAL_HOME"] = str(coral_dir)

    cfg = load_config()
    pid_path = cfg.daemon_pid_file

    if pid_path.is_file():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except ValueError:
            pid_path.unlink(missing_ok=True)
            typer.secho("Removed corrupt PID file.", fg=typer.colors.YELLOW)
        else:
            if pid_running(pid):
                typer.secho(
                    f"Daemon already running (PID {pid}).",
                    err=True,
                    fg=typer.colors.YELLOW,
                )
                raise typer.Exit(code=1)
            typer.secho(f"Removing stale PID file (PID {pid} not running).", fg=typer.colors.YELLOW)
            pid_path.unlink(missing_ok=True)

    passphrase = _unlock_passphrase_prompt()

    ensure_config_file_exists(home=coral_dir)

    try:
        run_daemon_blocking(home=coral_dir, passphrase=passphrase)
    except (VaultLockedError, VaultIntegrityError):
        typer.secho(
            "Incorrect passphrase or vault corrupted.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1) from None


@app.command("stop")
def stop_cmd(
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
) -> None:
    """Stop a Coral daemon using the PID file written by ``coral start``."""
    coral_dir = _home(home)
    os.environ["CORAL_HOME"] = str(coral_dir)

    cfg = load_config()
    pid_path = cfg.daemon_pid_file

    if not pid_path.is_file():
        typer.secho("No daemon running.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except ValueError:
        pid_path.unlink(missing_ok=True)
        typer.secho("Removed corrupt PID file.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1) from None

    if not pid_running(pid):
        pid_path.unlink(missing_ok=True)
        typer.secho("Removed stale PID file.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    typer.echo(f"Stopping Coral daemon (PID {pid})...")
    proc: psutil.Process | None = None
    try:
        proc = psutil.Process(pid)
        proc.terminate()
    except psutil.NoSuchProcess:
        pid_path.unlink(missing_ok=True)
        typer.echo("Daemon already exited.")
        raise typer.Exit(code=0) from None
    except psutil.Error:
        os.kill(pid, signal.SIGTERM)

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if not pid_path.is_file():
            typer.echo("Stopped.")
            raise typer.Exit(code=0)
        if not pid_running(pid):
            pid_path.unlink(missing_ok=True)
            typer.echo("Stopped.")
            raise typer.Exit(code=0)
        time.sleep(0.05)

    typer.secho(
        "Daemon did not exit after SIGTERM; sending SIGKILL. "
        "Orphaned Chromium processes may remain once Playwright lands.",
        err=True,
        fg=typer.colors.YELLOW,
    )
    try:
        if proc is not None:
            proc.kill()
        else:
            psutil.Process(pid).kill()
    except psutil.Error:
        with suppress(OSError):
            os.kill(pid, signal.SIGKILL)
    pid_path.unlink(missing_ok=True)


@app.command("mcp-stdio")
def mcp_stdio(
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
    agent_name: str = typer.Option(
        "stdio",
        "--agent-name",
        help="Identity recorded in audit_log.agent_id for this MCP session.",
    ),
) -> None:
    """Run Coral's MCP server over stdio (for MCP clients that spawn a subprocess).

    Opens the vault using ``CORAL_PASSPHRASE`` (interactive prompt if unset). The
    daemon may also be running concurrently; SQLCipher tolerates multiple readers
    on the same database file.
    """
    import asyncio

    from coral.mcp_server import run_mcp_stdio
    from coral.vault import unlock_vault

    coral_dir = _home(home)
    os.environ["CORAL_HOME"] = str(coral_dir)
    passphrase = _unlock_passphrase_prompt()

    async def _run() -> None:
        vault = await unlock_vault(home=coral_dir, passphrase=passphrase)
        try:
            await run_mcp_stdio(vault=vault, agent_name=agent_name)
        finally:
            await vault.close()

    asyncio.run(_run())


@app.command("status")
def status(
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
) -> None:
    """Show whether the vault file exists (daemon details arrive in week 1)."""
    coral_dir = _home(home)
    db = vault_db_path(coral_dir)
    typer.echo(f"Coral home: {coral_dir}")
    typer.echo(f"Vault DB: {'present' if db.is_file() else 'missing'} ({db})")


@app.command("list")
def list_command() -> None:
    """List captured sessions (vault + HTTP integration lands in week 1)."""
    raise NotImplementedError("Session listing is implemented in week 1 (spec §9).")


@app.command("revoke")
def revoke(site: str = typer.Argument(..., help="Site/origin to revoke (placeholder).")) -> None:
    """Revoke a stored session (week 1+)."""
    raise NotImplementedError(f"Revoke not implemented yet ({site=!r}).")


@app.command("export-audit")
def export_audit() -> None:
    """Export the local audit log (week 2+)."""
    raise NotImplementedError("export-audit arrives with audit tooling (spec §3.1).")


@app.command("policy")
def policy(site: str = typer.Argument(..., help="Origin to inspect/edit (placeholder).")) -> None:
    """View or edit per-site policy (week 3)."""
    raise NotImplementedError(f"policy command not implemented yet ({site=!r}).")
