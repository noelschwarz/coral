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
from typing import Any

import psutil
import typer

from coral import __version__
from coral.cli_client import as_list_of_dicts as _as_list_of_dicts
from coral.cli_client import http_request
from coral.cli_client import read_cli_token as _read_cli_token
from coral.config import Config, ensure_config_file_exists, load_config
from coral.crypto import MIN_PASSPHRASE_LENGTH, hash_token
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


def _unlock_passphrase_prompt(coral_home: Path | None = None) -> str:
    """Resolve a passphrase for unlocking an existing vault.

    Order: ``CORAL_PASSPHRASE`` env var → OS keychain (if a ``coral_home`` is
    given and a backend is available) → interactive prompt. If none of those
    yield a passphrase and there's no TTY (e.g. launchd/systemd-loaded daemon),
    exits with a clear message instead of hanging.
    """
    env = os.environ.get("CORAL_PASSPHRASE", "").strip()
    if env:
        return env
    if coral_home is not None:
        from coral import keychain as kc

        if kc.is_available():
            try:
                return kc.retrieve(coral_home)
            except kc.KeychainNotFound:
                pass
            except kc.KeychainError as exc:
                typer.secho(
                    f"keychain read failed ({exc}); falling back to prompt.",
                    err=True,
                    fg=typer.colors.YELLOW,
                )
    import sys as _sys

    if not _sys.stdin.isatty():
        typer.secho(
            "No CORAL_PASSPHRASE in environment, no keychain entry, and no TTY "
            "to prompt. Run `coral keychain store` to stash the passphrase, or "
            "set CORAL_PASSPHRASE.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
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
        from coral.vault import seed_bundled_behavior_packs

        ensure_config_file_exists(home=coral_dir)
        vault = await Vault.initialize(coral_dir, passphrase)
        try:
            seeded = await seed_bundled_behavior_packs(vault)
        finally:
            await vault.close()
        if seeded:
            typer.echo(f"Loaded {seeded} bundled behavior packs.")

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

    passphrase = _unlock_passphrase_prompt(coral_dir)

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


@app.command("up")
def up(
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
    no_clipboard: bool = typer.Option(
        False,
        "--no-clipboard",
        help="Don't copy the handshake challenge to the system clipboard.",
    ),
    foreground: bool = typer.Option(
        False,
        "--foreground",
        "-f",
        help="Run the daemon in the foreground instead of detaching it.",
    ),
) -> None:
    """One-command setup: init the vault if needed, start the daemon, and copy
    the handshake challenge to the clipboard.

    Default behaviour daemonizes the daemon as a detached background process so
    you can close your terminal. Use ``--foreground`` to keep it attached, or
    ``coral install-service`` once you're ready for it to start automatically
    on login.
    """
    import subprocess
    import sys
    import time as _time

    coral_dir = _home(home)
    os.environ["CORAL_HOME"] = str(coral_dir)

    cfg = load_config()
    if cfg.daemon_pid_file.is_file():
        try:
            pid = int(cfg.daemon_pid_file.read_text(encoding="utf-8").strip())
        except ValueError:
            cfg.daemon_pid_file.unlink(missing_ok=True)
            pid = 0
        if pid and pid_running(pid):
            _present_pairing(coral_dir=coral_dir, no_clipboard=no_clipboard, already_up=True)
            return
        # Stale PID file — fall through and start fresh.
        cfg.daemon_pid_file.unlink(missing_ok=True)

    db_path = vault_db_path(coral_dir)
    if not db_path.is_file():
        typer.echo(f"Setting up Coral in {coral_dir} (first-time init)…")
        passphrase = _new_passphrase_prompt()
        if len(passphrase) < MIN_PASSPHRASE_LENGTH:
            typer.secho(
                f"Passphrase must be at least {MIN_PASSPHRASE_LENGTH} characters "
                "(engineering spec §6.2 / T9).",
                err=True,
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=2)
        os.environ["CORAL_PASSPHRASE"] = passphrase

        async def _do_init() -> None:
            from coral.vault import seed_bundled_behavior_packs

            ensure_config_file_exists(home=coral_dir)
            vault = await Vault.initialize(coral_dir, passphrase)
            try:
                await seed_bundled_behavior_packs(vault)
            finally:
                await vault.close()

        try:
            asyncio.run(_do_init())
        except VaultError as exc:
            typer.secho(f"init failed: {exc}", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1) from exc
        typer.secho(f"✓ Vault created at {db_path}", fg=typer.colors.GREEN)
    else:
        # Vault exists; we still need a passphrase for `coral start`.
        if not os.environ.get("CORAL_PASSPHRASE"):
            passphrase = _unlock_passphrase_prompt(coral_dir)
            os.environ["CORAL_PASSPHRASE"] = passphrase

    if foreground:
        typer.echo("Starting daemon in the foreground (Ctrl+C to stop)…")
        try:
            run_daemon_blocking(home=coral_dir, passphrase=os.environ["CORAL_PASSPHRASE"])
        except (VaultLockedError, VaultIntegrityError):
            typer.secho("Incorrect passphrase or vault corrupted.", err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1) from None
        return

    # Daemonize: detach the daemon into its own session so it survives the
    # terminal closing. stdout/stderr → coral.log in CORAL_HOME.
    log_path = coral_dir / "coral.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab", buffering=0) as log_fh:
        proc = subprocess.Popen(
            [sys.executable, "-m", "coral", "start", "--home", str(coral_dir)],
            env={**os.environ, "CORAL_HOME": str(coral_dir)},
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    typer.echo("Starting daemon…")
    # Wait up to 15s for the daemon to come online + write the pairing file.
    deadline = _time.monotonic() + 15.0
    pairing_path = coral_dir / ".pairing_challenge"
    while _time.monotonic() < deadline:
        if proc.poll() is not None:
            # Daemon exited; show the log tail and bail.
            tail = (
                log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
                if log_path.is_file()
                else "(no log)"
            )
            typer.secho(
                f"Daemon exited (code {proc.returncode}). Last log lines:\n\n{tail}",
                err=True,
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1)
        if pairing_path.is_file() and cfg.daemon_pid_file.is_file():
            break
        _time.sleep(0.2)
    else:
        typer.secho(
            "Daemon didn't come online in 15s. Check ~/.coral/coral.log.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    _present_pairing(coral_dir=coral_dir, no_clipboard=no_clipboard, already_up=False)


def _present_pairing(*, coral_dir: Path, no_clipboard: bool, already_up: bool) -> None:
    """Read the pairing challenge file, copy to clipboard, and print next steps."""
    from coral.clipboard import copy_to_clipboard

    pairing_path = coral_dir / ".pairing_challenge"
    if not pairing_path.is_file():
        if already_up:
            typer.secho(
                "Daemon is already running, but the pairing challenge file is gone "
                "(consumed by a previous pair). If the Coral extension is already "
                "paired, you're done — just open the popup. To force a re-pair, "
                "restart the daemon: `coral stop && coral up`.",
                fg=typer.colors.YELLOW,
            )
            return
        typer.secho("Pairing challenge file missing — daemon may not be fully up yet.", err=True)
        raise typer.Exit(code=1)

    challenge = pairing_path.read_text(encoding="utf-8").strip()
    copied = False if no_clipboard else copy_to_clipboard(challenge)

    bar = "─" * 50
    typer.echo("")
    typer.secho(bar, fg=typer.colors.CYAN)
    if already_up:
        typer.secho("Coral daemon is already running.", fg=typer.colors.GREEN, bold=True)
    else:
        typer.secho("Coral daemon is up and running.", fg=typer.colors.GREEN, bold=True)
    typer.secho(bar, fg=typer.colors.CYAN)
    typer.echo("")
    typer.echo("Pairing challenge:")
    typer.secho(f"    {challenge}", bold=True)
    typer.echo("")
    if copied:
        typer.secho("✓ Copied to your clipboard.", fg=typer.colors.GREEN)
    elif not no_clipboard:
        typer.secho(
            "(Clipboard tool not found. Copy the challenge above by hand.)",
            fg=typer.colors.YELLOW,
        )
    typer.echo("")
    typer.echo("Next: click the Coral extension icon and click Pair.")
    typer.echo("(The popup auto-detects the clipboard challenge.)")
    typer.echo("")
    typer.echo("Useful commands:")
    typer.echo("  coral status     # check daemon liveness")
    typer.echo("  coral list       # list captured sessions")
    typer.echo("  coral stop       # stop the daemon")
    typer.echo("")
    typer.echo("For daily use, install as a service so the daemon starts on login:")
    typer.echo("  coral install-service")
    typer.echo("")


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
    passphrase = _unlock_passphrase_prompt(coral_dir)

    async def _run() -> None:
        vault = await unlock_vault(home=coral_dir, passphrase=passphrase)
        try:
            await run_mcp_stdio(vault=vault, agent_name=agent_name, coral_home=coral_dir)
        finally:
            await vault.close()

    asyncio.run(_run())


def _http_get(url: str, *, token: str, timeout: float = 5.0) -> tuple[int, dict[str, Any]]:
    return http_request("GET", url, token=token, timeout=timeout)


def _http_delete(url: str, *, token: str, timeout: float = 5.0) -> int:
    code, _ = http_request("DELETE", url, token=token, timeout=timeout)
    return code


@app.command("status")
def status(
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
) -> None:
    """Show daemon liveness, agent count, session count, and vault location."""
    coral_dir = _home(home)
    db = vault_db_path(coral_dir)
    typer.echo(f"Coral home: {coral_dir}")
    typer.echo(f"Vault DB: {'present' if db.is_file() else 'missing'} ({db})")

    cfg = load_config()
    pid_path = cfg.daemon_pid_file
    daemon_alive = False
    pid = None
    if pid_path.is_file():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
            daemon_alive = pid_running(pid)
        except ValueError:
            pid = None

    if not daemon_alive:
        typer.echo("Daemon: not running")
        return
    typer.echo(f"Daemon: running (PID {pid})")

    cli_token = _read_cli_token(coral_dir)
    if cli_token is None:
        typer.echo("Bridge token: missing (cannot query agent/session counts without it)")
        return
    base = f"http://{cfg.http_host}:{cfg.http_port}"

    sessions_status, sessions_payload = _http_get(f"{base}/sessions", token=cli_token)
    s_items = _as_list_of_dicts(sessions_payload.get("sessions"))
    if sessions_status == 200 and s_items is not None:
        active = sum(1 for s in s_items if s.get("status") == "active")
        typer.echo(f"Sessions: {active} active / {len(s_items)} total")
    else:
        typer.echo(f"Sessions: query failed (status={sessions_status})")

    tokens_status, tokens_payload = _http_get(f"{base}/tokens", token=cli_token)
    t_items = _as_list_of_dicts(tokens_payload.get("tokens"))
    if tokens_status == 200 and t_items is not None:
        names = sorted({str(r["name"]) for r in t_items if "name" in r})
        typer.echo(f"Connected agents: {len(t_items)} ({', '.join(names) if names else '—'})")
    else:
        typer.echo(f"Tokens: query failed (status={tokens_status})")


@app.command("audit")
def audit_command(
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
    since: int | None = typer.Option(
        None,
        "--since",
        help="Unix timestamp lower bound (default: 24h ago).",
    ),
    limit: int = typer.Option(50, "--limit", min=1, max=1000),
    event_type: str | None = typer.Option(
        None, "--event-type", help="Substring filter on event_type."
    ),
) -> None:
    """Query the audit log via the running daemon's HTTP API."""
    coral_dir = _home(home)
    cfg = load_config()
    pid_path = cfg.daemon_pid_file
    if not pid_path.is_file():
        typer.secho("Daemon not running. Start it with `coral start`.", err=True)
        raise typer.Exit(code=1)
    cli_token = _read_cli_token(coral_dir)
    if cli_token is None:
        typer.secho("Bridge token missing. Restart `coral start`.", err=True)
        raise typer.Exit(code=1)

    base = f"http://{cfg.http_host}:{cfg.http_port}"
    qs = f"limit={limit}" + (f"&since={since}" if since is not None else "")
    status_code, payload = _http_get(f"{base}/audit?{qs}", token=cli_token)
    entries = _as_list_of_dicts(payload.get("entries"))
    if status_code != 200 or entries is None:
        typer.secho(f"audit query failed (status={status_code})", err=True)
        raise typer.Exit(code=1)

    for raw in entries:
        et = str(raw.get("event_type", ""))
        if event_type and event_type not in et:
            continue
        ts = str(raw.get("timestamp", "?"))
        agent = str(raw.get("agent_id") or "—")
        origin = str(raw.get("origin") or "—")
        detail = str(raw.get("detail") or "")
        typer.echo(f"{ts}  {et:<28} agent={agent:<16} origin={origin:<24} {detail}")


@app.command("panic")
def panic_command(
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the confirmation prompt (CI/scripted use).",
    ),
) -> None:
    """Revoke every token, revoke every session, then stop the daemon.

    The trust-recovery primitive: if you suspect any agent or token has been
    compromised, this puts Coral into a known-safe state in one command. The
    vault file and stored passphrase metadata are preserved — your captured
    sessions are *marked revoked*, not deleted, and you can re-init or re-pair
    afterwards.
    """
    coral_dir = _home(home)
    os.environ["CORAL_HOME"] = str(coral_dir)
    cfg = load_config()

    if not yes:
        typer.confirm(
            "Revoke ALL tokens and ALL sessions, then stop the Coral daemon. Continue?",
            abort=True,
        )

    pid_path = cfg.daemon_pid_file
    cli_token = _read_cli_token(coral_dir)
    daemon_alive = (
        pid_path.is_file() and cli_token is not None and _is_pid_alive_from_path(pid_path)
    )

    if daemon_alive and cli_token is not None:
        _panic_via_http(cfg=cfg, token=cli_token)
    else:
        _panic_via_vault(coral_dir)

    typer.secho("Panic complete. All tokens and sessions revoked.", fg=typer.colors.GREEN)


def _is_pid_alive_from_path(pid_path: Path) -> bool:
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return pid_running(pid)


def _panic_via_http(*, cfg: Config, token: str) -> None:
    """Revoke everything via HTTP. Order matters:

    1. Revoke every session first (still need an authenticated token to do this).
    2. Revoke every *other* token next.
    3. Revoke the panic-driver's own token last — after this, any further HTTP
       call would 401, which is intentional: there is no remaining authority.
    4. SIGTERM the daemon.
    """
    base = f"http://{cfg.http_host}:{cfg.http_port}"
    own_hash = hash_token(token)

    _, sessions_payload = _http_get(f"{base}/sessions", token=token)
    for s in _as_list_of_dicts(sessions_payload.get("sessions")) or []:
        sid = s.get("id")
        if isinstance(sid, str):
            _http_delete(f"{base}/sessions/{sid}", token=token)

    _, tokens_payload = _http_get(f"{base}/tokens", token=token)
    other_hashes: list[str] = []
    self_present = False
    for tok in _as_list_of_dicts(tokens_payload.get("tokens")) or []:
        th = tok.get("token_hash")
        if not isinstance(th, str):
            continue
        if th == own_hash:
            self_present = True
        else:
            other_hashes.append(th)
    for th in other_hashes:
        _http_delete(f"{base}/tokens/{th}", token=token)
    if self_present:
        _http_delete(f"{base}/tokens/{own_hash}", token=token)

    pid_path = cfg.daemon_pid_file
    if pid_path.is_file():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return
        with suppress(psutil.Error, OSError):
            psutil.Process(pid).terminate()


def _panic_via_vault(coral_dir: Path) -> None:
    """Daemon is not running — open the vault directly and zero everything."""
    import asyncio

    from coral.vault import unlock_vault

    passphrase = _unlock_passphrase_prompt(coral_dir)

    async def _run() -> None:
        vault = await unlock_vault(home=coral_dir, passphrase=passphrase)
        try:
            for tok in await vault.list_tokens():
                await vault.delete_token(tok.token_hash)
            for s in await vault.list_sessions():
                if s.status == "active":
                    await vault.revoke_session(s.id)
        finally:
            await vault.close()

    asyncio.run(_run())


@app.command("list")
def list_command(
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
) -> None:
    """List captured sessions via the running daemon."""
    coral_dir = _home(home)
    cfg = load_config()
    if not cfg.daemon_pid_file.is_file():
        typer.secho("Daemon not running. Start it with `coral start`.", err=True)
        raise typer.Exit(code=1)
    cli_token = _read_cli_token(coral_dir)
    if cli_token is None:
        typer.secho("Bridge token missing. Restart `coral start`.", err=True)
        raise typer.Exit(code=1)
    base = f"http://{cfg.http_host}:{cfg.http_port}"
    code, payload = _http_get(f"{base}/sessions", token=cli_token)
    rows = _as_list_of_dicts(payload.get("sessions"))
    if code != 200 or rows is None:
        typer.secho(f"list failed (status={code})", err=True)
        raise typer.Exit(code=1)
    if not rows:
        typer.echo("(no sessions captured)")
        return
    for r in rows:
        sid = r.get("id", "?")
        origin = r.get("origin", "?")
        status_field = r.get("status", "?")
        label = r.get("label") or ""
        typer.echo(f"{sid}  {status_field:<8} {origin}  {label}")


@app.command("revoke")
def revoke(
    site: str = typer.Argument(..., help="Origin (e.g. https://example.com) to revoke."),
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
) -> None:
    """Revoke every session matching ``site`` (origin)."""
    coral_dir = _home(home)
    cfg = load_config()
    cli_token = _read_cli_token(coral_dir)
    if cli_token is None or not cfg.daemon_pid_file.is_file():
        typer.secho("Daemon not running. Start it with `coral start`.", err=True)
        raise typer.Exit(code=1)
    base = f"http://{cfg.http_host}:{cfg.http_port}"
    _, payload = _http_get(f"{base}/sessions", token=cli_token)
    rows = _as_list_of_dicts(payload.get("sessions")) or []
    matches = [r for r in rows if r.get("origin") == site and r.get("status") == "active"]
    if not matches:
        typer.secho(f"No active sessions for {site}.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)
    for r in matches:
        sid = r.get("id")
        if isinstance(sid, str):
            code = _http_delete(f"{base}/sessions/{sid}", token=cli_token)
            if code == 204:
                typer.echo(f"revoked {sid}")
            else:
                typer.secho(f"failed to revoke {sid} (status={code})", err=True)


def _http_put(url: str, *, token: str, body: dict[str, Any], timeout: float = 5.0) -> int:
    code, _ = http_request("PUT", url, token=token, body=body, timeout=timeout)
    return code


def _http_post(url: str, *, token: str, body: dict[str, Any], timeout: float = 5.0) -> int:
    code, _ = http_request("POST", url, token=token, body=body, timeout=timeout)
    return code


def _daemon_token_or_exit(coral_dir: Path) -> tuple[Config, str]:
    cfg = load_config()
    if not cfg.daemon_pid_file.is_file():
        typer.secho("Daemon not running. Start it with `coral start`.", err=True)
        raise typer.Exit(code=1)
    token = _read_cli_token(coral_dir)
    if token is None:
        typer.secho("Bridge token missing. Restart `coral start`.", err=True)
        raise typer.Exit(code=1)
    return cfg, token


policy_app = typer.Typer(no_args_is_help=True, help="View or edit per-site policy YAML.")
app.add_typer(policy_app, name="policy")


@policy_app.command("get")
def policy_get(
    origin: str = typer.Argument(..., help="Origin e.g. https://example.com"),
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
) -> None:
    """Print the YAML policy for ``origin``, or report 'not found'."""
    coral_dir = _home(home)
    cfg, token = _daemon_token_or_exit(coral_dir)
    base = f"http://{cfg.http_host}:{cfg.http_port}"
    from urllib.parse import quote

    code, payload = _http_get(f"{base}/policies/{quote(origin, safe='')}", token=token)
    if code == 404:
        typer.secho(f"No policy stored for {origin}.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)
    if code != 200:
        typer.secho(f"policy get failed (status={code})", err=True)
        raise typer.Exit(code=1)
    typer.echo(str(payload.get("yaml_body", "")))


@policy_app.command("put")
def policy_put(
    origin: str = typer.Argument(..., help="Origin e.g. https://example.com"),
    file: Path = typer.Option(..., "--file", "-f", help="YAML file to upload."),
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
) -> None:
    """Upload ``file`` as the per-origin policy YAML."""
    coral_dir = _home(home)
    cfg, token = _daemon_token_or_exit(coral_dir)
    base = f"http://{cfg.http_host}:{cfg.http_port}"
    from urllib.parse import quote

    code = _http_put(
        f"{base}/policies/{quote(origin, safe='')}",
        token=token,
        body={"yaml_body": file.read_text(encoding="utf-8")},
    )
    if code == 204:
        typer.echo(f"policy updated for {origin}")
    else:
        typer.secho(f"policy put failed (status={code})", err=True)
        raise typer.Exit(code=1)


reviews_app = typer.Typer(no_args_is_help=True, help="Inspect and decide pending reviews.")
app.add_typer(reviews_app, name="reviews")


@reviews_app.command("list")
def reviews_list(
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
) -> None:
    coral_dir = _home(home)
    cfg, token = _daemon_token_or_exit(coral_dir)
    base = f"http://{cfg.http_host}:{cfg.http_port}"
    code, payload = _http_get(f"{base}/reviews", token=token)
    items = _as_list_of_dicts(payload.get("reviews"))
    if code != 200 or items is None:
        typer.secho(f"reviews list failed (status={code})", err=True)
        raise typer.Exit(code=1)
    if not items:
        typer.echo("(no pending reviews)")
        return
    for r in items:
        rid = r.get("id", "?")
        agent = r.get("agent_id") or "—"
        action = r.get("action_type", "?")
        detail = r.get("action_detail", "")
        typer.echo(f"{rid}  agent={agent:<16} action={action:<24} {detail}")


def _decide(review_id: str, decision: str, home: Path | None) -> None:
    coral_dir = _home(home)
    cfg, token = _daemon_token_or_exit(coral_dir)
    base = f"http://{cfg.http_host}:{cfg.http_port}"
    code = _http_post(
        f"{base}/reviews/{review_id}/decision",
        token=token,
        body={"decision": decision},
    )
    if code == 204:
        typer.secho(f"review {review_id} {decision}", fg=typer.colors.GREEN)
    elif code == 404:
        typer.secho(f"review {review_id} not found", err=True)
        raise typer.Exit(code=1)
    elif code == 409:
        typer.secho(f"review {review_id} already decided", err=True)
        raise typer.Exit(code=1)
    else:
        typer.secho(f"decision failed (status={code})", err=True)
        raise typer.Exit(code=1)


@app.command("approve")
def approve_cmd(
    review_id: str = typer.Argument(..., help="The review_id from `coral reviews list`."),
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
) -> None:
    """Approve a pending review."""
    _decide(review_id, "approved", home)


@app.command("deny")
def deny_cmd(
    review_id: str = typer.Argument(..., help="The review_id from `coral reviews list`."),
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
) -> None:
    """Deny a pending review."""
    _decide(review_id, "denied", home)


# ---- mcp install / uninstall / status ---------------------------------------


mcp_app = typer.Typer(
    no_args_is_help=True,
    help="Wire Coral into MCP clients (Claude Desktop, Cursor, Claude Code).",
)
app.add_typer(mcp_app, name="mcp")


def _mcp_known_clients_str() -> str:
    from coral.mcp_install import KNOWN_CLIENTS

    return ", ".join(KNOWN_CLIENTS)


@mcp_app.command("install")
def mcp_install_cmd(
    client: str = typer.Option(
        ...,
        "--client",
        "-c",
        help="Which MCP client to configure: claude-desktop, cursor, or claude-code.",
    ),
    name: str = typer.Option(
        "coral",
        "--name",
        "-n",
        help="Name for the server entry in the client's config.",
    ),
    home: Path | None = typer.Option(
        None,
        "--home",
        help=(
            "Coral data directory. Only written into the client config if "
            "explicitly passed or if CORAL_HOME is set; otherwise the client "
            "uses Coral's default (~/.coral)."
        ),
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite an existing entry with the same name.",
    ),
) -> None:
    """Add Coral as an MCP server in the specified client's config file.

    After running this, restart the client (Claude Desktop, Cursor, Claude
    Code) to load the new server. The client will spawn ``coral mcp-stdio``
    on demand.
    """
    from coral import mcp_install as mi

    if client not in mi.KNOWN_CLIENTS:
        typer.secho(
            f"Unknown client {client!r}. Known: {_mcp_known_clients_str()}.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2)

    coral_dir: Path | None = None
    if home is not None:
        coral_dir = home.expanduser().resolve()
    elif os.environ.get("CORAL_HOME"):
        coral_dir = Path(os.environ["CORAL_HOME"]).expanduser().resolve()

    try:
        result = mi.install(client, name=name, coral_home=coral_dir, force=force)
    except mi.MCPInstallError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    if result.created_config_file:
        typer.echo(f"Created {result.config_path} (fresh config).")
    if result.overwrote_existing:
        typer.secho(
            f"Overwrote existing {name!r} entry in {result.config_path}.",
            fg=typer.colors.YELLOW,
        )
    typer.secho(
        f"✓ Added {name!r} to {result.client.label} config.",
        fg=typer.colors.GREEN,
    )
    typer.echo("")
    typer.echo(f"Restart {result.client.label} to load the new MCP server.")


@mcp_app.command("uninstall")
def mcp_uninstall_cmd(
    client: str = typer.Option(..., "--client", "-c", help="Which MCP client to update."),
    name: str = typer.Option("coral", "--name", "-n", help="Name of the server entry to remove."),
) -> None:
    """Remove Coral from the specified client's MCP config. Idempotent."""
    from coral import mcp_install as mi

    if client not in mi.KNOWN_CLIENTS:
        typer.secho(
            f"Unknown client {client!r}. Known: {_mcp_known_clients_str()}.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2)

    try:
        removed = mi.uninstall(client, name=name)
    except mi.MCPInstallError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    if removed:
        typer.secho(
            f"✓ Removed {name!r} from {mi.get_client(client).label} config.",
            fg=typer.colors.GREEN,
        )
    else:
        typer.echo(f"No {name!r} entry to remove (config absent or entry not present).")


@mcp_app.command("status")
def mcp_status_cmd(
    client: str = typer.Option(..., "--client", "-c", help="Which MCP client to inspect."),
    name: str = typer.Option("coral", "--name", "-n", help="Name of the server entry to check."),
) -> None:
    """Print the current MCP server entry for Coral in the client config."""
    import json as _json

    from coral import mcp_install as mi

    if client not in mi.KNOWN_CLIENTS:
        typer.secho(
            f"Unknown client {client!r}. Known: {_mcp_known_clients_str()}.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2)

    c = mi.get_client(client)
    typer.echo(f"Client:      {c.label}")
    typer.echo(f"Config file: {c.config_path}")

    entry = mi.get_entry(client, name=name)
    if entry is None:
        typer.echo(f"Entry {name!r}: not installed")
        return
    typer.echo(f"Entry {name!r}:")
    typer.echo(_json.dumps(entry, indent=2))


# ---- keychain ---------------------------------------------------------------


keychain_app = typer.Typer(
    no_args_is_help=True,
    help="Manage the OS-keychain-stored vault passphrase (ADR-017).",
)
app.add_typer(keychain_app, name="keychain")


@keychain_app.command("store")
def keychain_store_cmd(
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
) -> None:
    """Prompt for the vault passphrase and store it in the OS keychain.

    Verifies the passphrase actually unlocks the vault before storing.
    """
    from coral import keychain as kc
    from coral.vault import unlock_vault

    coral_dir = _home(home)

    if not kc.is_available():
        typer.secho(
            "OS keychain not available on this platform. "
            "macOS needs `security` (always present); Linux needs `secret-tool` "
            "from libsecret-tools.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    db_path = vault_db_path(coral_dir)
    if not db_path.is_file():
        typer.secho(
            f"No vault at {db_path}. Run `coral up` first to create one.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    passphrase = os.environ.get("CORAL_PASSPHRASE", "").strip()
    if not passphrase:
        passphrase = typer.prompt("Vault passphrase", hide_input=True)

    async def _verify() -> None:
        vault = await unlock_vault(home=coral_dir, passphrase=passphrase)
        await vault.close()

    try:
        asyncio.run(_verify())
    except (VaultLockedError, VaultIntegrityError):
        typer.secho(
            "That passphrase doesn't unlock the vault — nothing stored.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1) from None

    try:
        kc.store(coral_dir, passphrase)
    except kc.KeychainError as exc:
        typer.secho(f"keychain store failed: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    typer.secho(
        f"✓ Stored passphrase for {coral_dir} in the OS keychain.",
        fg=typer.colors.GREEN,
    )


@keychain_app.command("clear")
def keychain_clear_cmd(
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
) -> None:
    """Remove the stored passphrase from the OS keychain. Idempotent."""
    from coral import keychain as kc

    coral_dir = _home(home)
    if not kc.is_available():
        typer.secho(
            "OS keychain not available on this platform.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    try:
        removed = kc.delete(coral_dir)
    except kc.KeychainError as exc:
        typer.secho(f"keychain clear failed: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    if removed:
        typer.secho("✓ Removed keychain entry.", fg=typer.colors.GREEN)
    else:
        typer.echo("No keychain entry to remove (already absent).")


@keychain_app.command("status")
def keychain_status_cmd(
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
) -> None:
    """Print whether a passphrase is stored for this CORAL_HOME."""
    import sys as _sys

    from coral import keychain as kc

    coral_dir = _home(home)
    typer.echo(f"Coral home: {coral_dir}")
    if not kc.is_available():
        typer.echo(f"Backend:    unavailable (platform={_sys.platform})")
        if _sys.platform.startswith("linux"):
            typer.echo("            install `libsecret-tools` to enable.")
        return

    backend = (
        "macOS Keychain (security)" if _sys.platform == "darwin" else "libsecret (secret-tool)"
    )
    typer.echo(f"Backend:    {backend}")
    try:
        kc.retrieve(coral_dir)
    except kc.KeychainNotFound:
        typer.echo("Entry:      not stored")
        return
    except kc.KeychainError as exc:
        typer.echo(f"Entry:      error ({exc})")
        return
    typer.echo("Entry:      present")


# ---- install-service / uninstall-service ------------------------------------


@app.command("install-service")
def install_service_cmd(
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
    passphrase_env: bool = typer.Option(
        False,
        "--passphrase-env",
        help=(
            "Write a CORAL_PASSPHRASE placeholder into the service file instead "
            "of using the OS keychain. You'll need to edit it to your real "
            "passphrase before the service can start. Convenient but puts the "
            "passphrase on disk (mode 0600)."
        ),
    ),
    no_keychain: bool = typer.Option(
        False,
        "--no-keychain",
        help=(
            "Don't store the passphrase in the OS keychain. The service file "
            "will have no passphrase anywhere; the daemon won't start until you "
            "run `coral up` manually after each reboot."
        ),
    ),
) -> None:
    """Install Coral as a user-level OS service (launchd / systemd --user).

    By default, prompts for the vault passphrase, verifies it, and stores it in
    the OS keychain (macOS Keychain Access / Linux Secret Service). The service
    file contains no secrets, and the daemon reads the passphrase from the
    keychain on startup.

    Use ``--passphrase-env`` to put the passphrase in the service file (mode
    0600) instead, or ``--no-keychain`` to require manual `coral up` after
    every reboot.

    macOS and Linux only. On Windows, run ``coral up`` manually or use Task
    Scheduler — see ADR-016.
    """
    from coral.service import (
        Platform,
        activate_service,
        current_platform,
        install_service,
    )

    plat = current_platform()
    if plat is Platform.OTHER:
        typer.secho(
            "coral install-service supports macOS and Linux only. "
            "On Windows, run `coral up` manually.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    if passphrase_env and no_keychain:
        typer.secho(
            "--passphrase-env and --no-keychain are mutually exclusive.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2)

    coral_dir = _home(home)
    use_keychain = not passphrase_env and not no_keychain
    if use_keychain:
        _ensure_keychain_passphrase(coral_dir)

    result = install_service(coral_home=coral_dir, passphrase_env=passphrase_env)
    typer.secho(f"✓ Wrote service file: {result.unit_path}", fg=typer.colors.GREEN)
    if result.needs_passphrase_edit:
        typer.secho(
            "⚠  CORAL_PASSPHRASE placeholder in the service file. "
            f"Edit {result.unit_path} and replace the placeholder with your "
            "real passphrase, then re-run `coral install-service`.",
            fg=typer.colors.YELLOW,
        )
        typer.echo("")
        typer.echo("Not activating the service yet — fix the placeholder first.")
        return

    typer.echo("Activating service…")
    ok, msg = activate_service()
    if ok:
        typer.secho(
            "✓ Service is running. It will start automatically on login.",
            fg=typer.colors.GREEN,
        )
        typer.echo("")
        typer.echo("  coral status        # verify daemon liveness")
        typer.echo("  coral uninstall-service   # remove it later")
    else:
        typer.secho("Service install wrote the file but couldn't activate:", err=True)
        typer.echo(msg)
        raise typer.Exit(code=1)


def _ensure_keychain_passphrase(coral_dir: Path) -> None:
    """For ``install-service``: ensure the OS keychain holds a valid passphrase
    for ``coral_dir``. Prompts, verifies against the vault, and stores."""
    from coral import keychain as kc

    if not kc.is_available():
        typer.secho(
            "OS keychain not available on this platform. Re-run with "
            "`--passphrase-env` (writes the passphrase to the service file) "
            "or `--no-keychain` (you'll run `coral up` manually after reboot).",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    db_path = vault_db_path(coral_dir)
    if not db_path.is_file():
        typer.secho(
            f"No vault at {db_path}. Run `coral up` first to create one.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    from coral.vault import unlock_vault

    async def _passphrase_unlocks(pw: str) -> bool:
        try:
            vault = await unlock_vault(home=coral_dir, passphrase=pw)
        except (VaultLockedError, VaultIntegrityError):
            return False
        await vault.close()
        return True

    # Reuse an existing keychain entry only if it still unlocks the vault —
    # otherwise the daemon would fail at startup and the user would have to
    # debug a service that "installed successfully" but doesn't run.
    try:
        existing = kc.retrieve(coral_dir)
    except kc.KeychainNotFound:
        existing = None
    except kc.KeychainError as exc:
        typer.secho(f"keychain lookup failed: {exc}", err=True, fg=typer.colors.YELLOW)
        existing = None

    if existing is not None:
        if asyncio.run(_passphrase_unlocks(existing)):
            typer.echo("Using existing keychain entry for the vault passphrase.")
            return
        typer.secho(
            "Stored keychain passphrase no longer unlocks the vault — re-prompting.",
            fg=typer.colors.YELLOW,
        )

    passphrase = os.environ.get("CORAL_PASSPHRASE", "").strip()
    if not passphrase:
        passphrase = typer.prompt(
            "Vault passphrase (will be stored in the OS keychain)", hide_input=True
        )

    if not asyncio.run(_passphrase_unlocks(passphrase)):
        typer.secho(
            "That passphrase doesn't unlock the vault — nothing stored.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    try:
        kc.store(coral_dir, passphrase)
    except kc.KeychainError as exc:
        typer.secho(f"keychain store failed: {exc}", err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    typer.secho("✓ Stored passphrase in the OS keychain.", fg=typer.colors.GREEN)


@app.command("uninstall-service")
def uninstall_service_cmd(
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
    keep_keychain: bool = typer.Option(
        False,
        "--keep-keychain",
        help="Don't remove the OS-keychain passphrase entry.",
    ),
) -> None:
    """Stop the OS-managed Coral service, remove its unit file, and clear the
    keychain passphrase entry (use ``--keep-keychain`` to preserve it)."""
    from coral.service import Platform, current_platform, uninstall_service

    plat = current_platform()
    if plat is Platform.OTHER:
        typer.secho("Nothing to uninstall on this platform.", err=True)
        raise typer.Exit(code=1)
    ok, msg = uninstall_service()
    typer.echo(msg)
    if not ok:
        raise typer.Exit(code=1)

    if not keep_keychain:
        from coral import keychain as kc

        if kc.is_available():
            coral_dir = _home(home)
            try:
                if kc.delete(coral_dir):
                    typer.echo("Removed keychain passphrase entry.")
            except kc.KeychainError as exc:
                typer.secho(
                    f"(could not clear keychain entry: {exc})",
                    fg=typer.colors.YELLOW,
                )

    typer.secho("✓ Service uninstalled.", fg=typer.colors.GREEN)


# ---- diagnose ---------------------------------------------------------------


@app.command("diagnose")
def diagnose_cmd(
    home: Path | None = typer.Option(None, "--home", help="Coral data directory."),
) -> None:
    """Print a human-readable install + security self-check.

    Useful when reporting issues, prepping for a security review, or
    debugging a fresh install. Never prints tokens, challenges, the
    passphrase, or any secret material.
    """
    import platform as _platform
    import sys as _sys

    from coral import __version__

    coral_dir = _home(home)
    ok = "[32m✓[0m"
    warn = "[33m⚠[0m"
    fail = "[31m✗[0m"

    typer.echo(f"coralbridge {__version__}")
    typer.echo(f"Python {_sys.version.split()[0]} on {_platform.platform()}")
    typer.echo("")

    # --- runtime deps ------------------------------------------------------
    typer.echo("Runtime dependencies:")
    _report_module_version("sqlcipher3", "sqlcipher3", ok, warn)
    _report_module_version("argon2-cffi", "argon2", ok, warn)
    _report_module_version("playwright", "playwright", ok, warn)
    _report_module_version("mcp", "mcp", ok, warn)
    typer.echo("")

    # --- file layout + permissions ----------------------------------------
    typer.echo("Coral home: " + str(coral_dir))
    _report_file(coral_dir / "vault.db", 0o600, ok, warn, fail)
    _report_file(coral_dir / "vault_meta.json", 0o644, ok, warn, fail)
    _report_file(coral_dir / "cli.token", 0o600, ok, warn, fail)
    _report_file(coral_dir / "coral.pid", 0o644, ok, warn, fail)
    typer.echo("")

    # --- env-var hygiene --------------------------------------------------
    typer.echo("Environment hygiene:")
    if os.environ.get("CORAL_PASSPHRASE"):
        typer.echo(f"  {warn}  CORAL_PASSPHRASE is set in the environment. Convenient for")
        typer.echo("       CI; leaks into shell history on dev machines.")
    else:
        typer.echo(f"  {ok}  CORAL_PASSPHRASE not set in environment.")
    if os.environ.get("CORAL_HTTP_HOST", "127.0.0.1") != "127.0.0.1":
        typer.echo(f"  {fail}  CORAL_HTTP_HOST != 127.0.0.1 (spec §6.2 T2).")
    else:
        typer.echo(f"  {ok}  HTTP bind address is 127.0.0.1.")
    typer.echo("")

    # --- daemon liveness --------------------------------------------------
    cfg = load_config()
    pid_path = cfg.daemon_pid_file
    daemon_pid: int | None = None
    if pid_path.is_file():
        try:
            daemon_pid = int(pid_path.read_text(encoding="utf-8").strip())
        except ValueError:
            typer.echo(f"  {warn}  PID file present but not a number.")
    if daemon_pid is None or not pid_running(daemon_pid):
        typer.echo("Daemon: not running")
        typer.echo("")
        typer.echo("(Run `coral start` to see further diagnostics.)")
        return
    typer.echo(f"Daemon: running (PID {daemon_pid})")

    cli_token = _read_cli_token(coral_dir)
    if cli_token is None:
        typer.echo(f"  {fail}  Bridge token missing — daemon won't accept CLI calls.")
        return
    base = f"http://{cfg.http_host}:{cfg.http_port}"
    health_status, _ = _http_get(f"{base}/healthz", token="x", timeout=2.0)
    if health_status == 200:
        typer.echo(f"  {ok}  /healthz responding on {base}.")
    else:
        typer.echo(f"  {warn}  /healthz returned {health_status}.")

    s_status, s_payload = _http_get(f"{base}/sessions", token=cli_token, timeout=3.0)
    s_items = _as_list_of_dicts(s_payload.get("sessions")) if s_status == 200 else None
    t_status, t_payload = _http_get(f"{base}/tokens", token=cli_token, timeout=3.0)
    t_items = _as_list_of_dicts(t_payload.get("tokens")) if t_status == 200 else None
    if s_items is not None and t_items is not None:
        active_sessions = sum(1 for s in s_items if s.get("status") == "active")
        typer.echo(
            f"  {ok}  {active_sessions} active / {len(s_items)} total sessions, "
            f"{len(t_items)} bearer token(s)."
        )

    typer.echo("")
    typer.echo("(See docs/security-review-prep.md for the full reviewer checklist.)")


def _report_module_version(dist_name: str, import_name: str, ok: str, warn: str) -> None:
    try:
        import importlib

        importlib.import_module(import_name)
        try:
            from importlib import metadata as _metadata

            ver = _metadata.version(dist_name)
        except Exception:
            ver = "(version unknown)"
        typer.echo(f"  {ok}  {dist_name} {ver}")
    except ImportError:
        typer.echo(f"  {warn}  {dist_name} not importable (some features unavailable)")


def _report_file(
    path: Path,
    expected_mode: int,
    ok: str,
    warn: str,
    fail: str,
) -> None:
    if not path.is_file():
        typer.echo(f"  {warn}  {path.name}: missing")
        return
    actual_mode = path.stat().st_mode & 0o777
    if actual_mode == expected_mode:
        typer.echo(f"  {ok}  {path.name}: present (mode {actual_mode:o})")
        return
    if actual_mode <= expected_mode:
        # Stricter than expected — fine.
        typer.echo(
            f"  {ok}  {path.name}: present (mode {actual_mode:o}, stricter than {expected_mode:o})"
        )
        return
    if path.name in ("vault.db", "cli.token"):
        # These leaking to world-readable is genuinely bad.
        typer.echo(
            f"  {fail}  {path.name}: mode {actual_mode:o} "
            f"(expected ≤{expected_mode:o}); "
            f"fix with `chmod {expected_mode:o} {path}`"
        )
    else:
        typer.echo(f"  {warn}  {path.name}: mode {actual_mode:o} (expected {expected_mode:o})")
