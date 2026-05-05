"""Typer CLI entrypoint (spec §3.1 CLI + §7).

Commands are introduced incrementally across the 4-week plan. This module owns user
interaction boundaries like passphrase prompts (never log passphrases).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import typer

from coral import __version__
from coral.config import ensure_config_file_exists, load_config
from coral.daemon import run_daemon_blocking
from coral.paths import coral_home, daemon_pid_path, vault_db_path
from coral.vault import VaultError, init_vault, validate_vault_unlock

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Coral: local-first browser session bridge (daemon + CLI + Chrome extension).",
)


@app.callback(invoke_without_command=True)
def _main_callback(  # pyright: ignore[reportUnusedFunction]  # registered by Typer
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


def _read_passphrase(*, prompt: str) -> str:
    env = os.environ.get("CORAL_PASSPHRASE", "").strip()
    if env:
        return env
    return typer.prompt(prompt, hide_input=True)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


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
        help="(Unsafe) Reserved for future reset flows; currently unused.",
        hidden=True,
    ),
) -> None:
    """Create a new encrypted vault at ~/.coral/vault.db (configurable via --home / $CORAL_HOME)."""
    _ = force
    coral_dir = _home(home)
    os.environ["CORAL_HOME"] = str(coral_dir)

    passphrase = _read_passphrase(prompt="New vault passphrase")
    confirm = _read_passphrase(prompt="Confirm passphrase")
    if passphrase != confirm:
        typer.secho("Passphrases do not match; aborting.", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=2)

    try:
        ensure_config_file_exists(home=coral_dir)
        path = init_vault(home=coral_dir, passphrase=passphrase)
    except VaultError as exc:
        typer.secho(f"init failed: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Initialized encrypted vault at {path}")


@app.command()
def start(
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
    foreground: bool = typer.Option(False, "--foreground", help="Run daemon in the foreground."),
) -> None:
    """Start the Coral daemon (HTTP + MCP HTTP) if it is not already running."""
    coral_dir = _home(home)
    os.environ["CORAL_HOME"] = str(coral_dir)

    pid_path = daemon_pid_path(coral_dir)
    if pid_path.is_file():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except ValueError:
            pid_path.unlink(missing_ok=True)
        else:
            if _pid_alive(pid):
                typer.secho(
                    f"Daemon already running (pid={pid}).",
                    err=True,
                    fg=typer.colors.YELLOW,
                )
                raise typer.Exit(code=1)
            pid_path.unlink(missing_ok=True)

    passphrase = _read_passphrase(prompt="Vault passphrase")
    try:
        validate_vault_unlock(home=coral_dir, passphrase=passphrase)
    except VaultError as exc:
        typer.secho(f"Vault unavailable: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    cfg = load_config(home=coral_dir)
    if foreground:
        typer.echo("Starting Coral daemon in the foreground (Ctrl+C to stop).")
        run_daemon_blocking(home=coral_dir, passphrase=passphrase)
        return

    fd, tmp_path = tempfile.mkstemp(prefix="coral-pass-", suffix=".tmp", text=True)
    os.close(fd)
    secret_path = Path(tmp_path)
    secret_path.write_text(passphrase, encoding="utf-8")
    secret_path.chmod(0o600)

    env = os.environ.copy()
    env.update(
        {
            "CORAL_HOME": str(coral_dir),
            "CORAL_PASSPHRASE_FILE": str(secret_path),
        }
    )
    cmd = [sys.executable, "-m", "coral.daemon"]
    if os.name == "nt":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            cmd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
    else:
        subprocess.Popen(
            cmd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    # Wait briefly until healthz responds or fail fast if the child exits immediately.
    url = f"http://{cfg.http_host}:{cfg.http_port}/healthz"
    for _ in range(50):
        time.sleep(0.05)
        try:
            resp = httpx.get(url, timeout=0.2)
        except httpx.HTTPError:
            continue
        if resp.status_code == 200:
            typer.echo(f"Coral daemon started ({url} ok).")
            return

    typer.secho(
        "Daemon process spawned but /healthz did not become ready in time.",
        err=True,
        fg=typer.colors.RED,
    )
    raise typer.Exit(code=1)


@app.command("stop")
def stop_cmd(
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
) -> None:
    """Stop a background Coral daemon using the PID file written by `coral start`."""
    coral_dir = _home(home)
    pid_path = daemon_pid_path(coral_dir)
    if not pid_path.is_file():
        typer.secho("Daemon does not appear to be running (no PID file).", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except ValueError:
        pid_path.unlink(missing_ok=True)
        typer.secho("Removed corrupt PID file.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1) from None

    if not _pid_alive(pid):
        pid_path.unlink(missing_ok=True)
        typer.secho("Daemon PID is stale; removed PID file.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    typer.echo(f"Stopping Coral daemon (pid={pid})...")
    os.kill(pid, signal.SIGTERM)

    for _ in range(100):
        time.sleep(0.05)
        if not _pid_alive(pid):
            pid_path.unlink(missing_ok=True)
            typer.echo("Stopped.")
            return

    typer.secho(
        "Daemon did not exit cleanly; you may need to kill it manually.",
        err=True,
        fg=typer.colors.RED,
    )
    raise typer.Exit(code=1)


@app.command("mcp-stdio")
def mcp_stdio() -> None:
    """Run Coral's MCP server over stdio (for MCP clients that spawn a subprocess)."""
    import asyncio

    from coral.mcp_server import run_mcp_stdio

    asyncio.run(run_mcp_stdio())


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
