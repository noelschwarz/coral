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


def _read_cli_token(coral_dir: Path) -> str | None:
    token_path = coral_dir / "cli.token"
    if not token_path.is_file():
        return None
    try:
        return token_path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _http_get(url: str, *, token: str, timeout: float = 5.0) -> tuple[int, dict[str, Any]]:
    import json as _json
    import urllib.error
    import urllib.request
    from typing import cast

    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )

    def _parse(body: bytes) -> dict[str, Any]:
        if not body:
            return {}
        decoded = _json.loads(body.decode("utf-8"))
        return cast(dict[str, Any], decoded) if isinstance(decoded, dict) else {}

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, _parse(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, _parse(e.read())
    except (urllib.error.URLError, TimeoutError):
        return 0, {}


def _as_list_of_dicts(value: Any) -> list[dict[str, Any]] | None:
    """Coerce a parsed-JSON value into ``list[dict]`` or return None."""
    if not isinstance(value, list):
        return None
    out: list[dict[str, Any]] = []
    for item in value:  # pyright: ignore[reportUnknownVariableType]
        if isinstance(item, dict):
            out.append(item)  # pyright: ignore[reportUnknownArgumentType]
    return out


def _http_delete(url: str, *, token: str, timeout: float = 5.0) -> int:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, TimeoutError):
        return 0


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

    passphrase = _unlock_passphrase_prompt()

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


@app.command("policy")
def policy(site: str = typer.Argument(..., help="Origin to inspect/edit (placeholder).")) -> None:
    """View or edit per-site policy (week 3)."""
    raise NotImplementedError(f"policy command not implemented yet ({site=!r}).")
