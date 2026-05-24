"""Per-session Playwright lifecycle (spec §3.1, §7.2, ADR-010).

Each ``coral_open_session`` call launches its own Chromium process via
``launch_persistent_context``. Two reasons:

1. ``launch()`` + ``new_context()`` leaves Chromium's default browser context
   alongside ours — agents connecting via CDP see *both* contexts in
   ``Target.getTargets`` and must guess which one carries the restored cookies.
   Persistent context guarantees exactly one context exists.
2. The CDP endpoint we hand to the agent is naturally isolated per Chromium
   process — no other agent's contexts are visible. The shared-Chromium-with-
   CDP-target-filter optimization is a v1.x problem (ADR-010).

The route handler installed on every restored context is a **no-op-with-audit**
hook for v1: it audits the navigation, then calls ``route.continue_()``. Week
3's policy engine replaces the body of ``_route_handler`` without touching this
file's structure.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from coral import diag
from coral.policy import (
    PolicyEngine,
    default_policy_for_origin,
    load_policy_yaml,
)
from coral.restoration import apply_state_blob
from coral.vault import Vault, compress_blob, decompress_blob

if TYPE_CHECKING:
    from playwright.async_api import (
        BrowserContext,
        Playwright,
        Route,
    )


class SessionServerError(RuntimeError):
    """Base class for SessionServer failures."""


class SessionNotFoundError(SessionServerError):
    pass


class SessionNotActiveError(SessionServerError):
    pass


class SessionHandleNotFoundError(SessionServerError):
    pass


@dataclass
class OpenSession:
    """An open browser context owned by the daemon (spec §7.2)."""

    handle: str
    session_id: str
    agent_id: str
    purpose: str
    origin: str
    cdp_url: str
    opened_at: int
    expires_at: int
    context: BrowserContext  # persistent-context: also the Browser
    user_data_dir: Path
    engine: PolicyEngine
    timeout_task: asyncio.Task[None] | None = field(default=None, repr=False)

    def to_open_response(self) -> dict[str, Any]:
        """The §5.2 ``coral_open_session`` response shape."""
        return {
            "session_handle": self.handle,
            "cdp_url": self.cdp_url,
            "expires_at": self.expires_at,
            "policy_summary": self.engine.policy_summary,
        }


def _read_cdp_ws_url(port: int, *, timeout_s: float = 5.0) -> str:
    """Resolve the WS endpoint Chromium exposes for CDP clients.

    Chromium's ``--remote-debugging-port`` exposes a JSON metadata endpoint at
    ``/json/version`` whose ``webSocketDebuggerUrl`` field is the canonical
    address agents connect to.
    """
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=1.0
            ) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                ws = data.get("webSocketDebuggerUrl")
                if isinstance(ws, str) and ws.startswith("ws://"):
                    return ws
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ConnectionError) as e:
            last_err = e
            time.sleep(0.05)
    raise SessionServerError(
        f"chromium did not expose CDP /json/version on 127.0.0.1:{port} (last error: {last_err!r})"
    )


def _pick_tcp_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


CORAL_DAEMON_HOME_ENV = "CORAL_DAEMON_HOME"
"""Env var injected into every Chromium child process.

Used by :func:`recovery_kill_orphan_browsers` to identify browsers that
survived a crashed previous daemon run for the *same* ``$CORAL_HOME``.
"""


# ---- ADR-018: storage write-back helpers -------------------------------------


_COOKIE_KEY_FIELDS = ("name", "domain", "path")
_COOKIE_VALUE_FIELDS = ("value", "expires", "httpOnly", "secure", "sameSite")


def _glob_literal_prefix(pattern: str) -> str:
    """Return ``pattern`` truncated at the first glob metacharacter.

    ``"/api/v1/**" -> "/api/v1/"``; ``"/issues" -> "/issues"``; ``"**" -> ""``.
    """
    for i, ch in enumerate(pattern):
        if ch in "*?[":
            return pattern[:i]
    return pattern


def _cookie_path_allowed(cookie_path: str, allowed_paths: list[str]) -> bool:
    """Decide whether a cookie scoped to ``cookie_path`` is policy-admissible.

    Semantics (ADR-018):

    - A cookie at ``/`` applies to anything under the origin. Admit if the
      policy has any ``allowed_paths`` at all.
    - A cookie at ``/foo`` applies to URLs starting with ``/foo``. Admit if
      any ``allowed_paths`` entry's literal prefix starts with ``cookie_path``
      (i.e. the policy allows at least one URL the cookie applies to).
    """
    if not allowed_paths:
        return False
    if cookie_path == "/":
        return True
    for pattern in allowed_paths:
        prefix = _glob_literal_prefix(pattern)
        if prefix.startswith(cookie_path):
            return True
    return False


def _cookie_key(c: dict[str, Any]) -> tuple[str, str, str]:
    """Stable identity for diffing: ``(name, domain, path)``."""
    return (
        str(c.get("name", "")),
        str(c.get("domain", "")),
        str(c.get("path", "/")),
    )


def _cookies_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Equality across the value-bearing fields of a cookie."""
    return all(a.get(name) == b.get(name) for name in _COOKIE_VALUE_FIELDS)


class SessionServer:
    """Owns every open Playwright session for the daemon's lifetime."""

    def __init__(
        self,
        *,
        vault: Vault,
        max_duration_minutes: int,
        coral_home: Path | None = None,
        headless: bool = True,
    ) -> None:
        self._vault = vault
        self._max_duration_seconds = max_duration_minutes * 60
        self._coral_home = coral_home
        self._headless = headless
        self._handles: dict[str, OpenSession] = {}
        self._lock = asyncio.Lock()
        # Serializes the port-pick → Chromium-launch handoff (ADR-010).
        self._launch_lock = asyncio.Lock()
        self._playwright: Playwright | None = None

    async def _ensure_playwright(self) -> Playwright:
        if self._playwright is None:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
        return self._playwright

    async def open(
        self,
        *,
        session_id: str,
        agent_id: str,
        purpose: str,
    ) -> OpenSession:
        """Spawn a browser, restore the session, return the agent-facing handle."""
        record = await self._vault.get_session(session_id)
        if record is None:
            raise SessionNotFoundError(session_id)
        if record.status != "active":
            raise SessionNotActiveError(f"session {session_id} status={record.status}")

        try:
            blob = decompress_blob(record.state_blob)
        except Exception as exc:
            diag.error("session.open.failed", reason="decompress_failed", session_id=session_id)
            raise SessionServerError(f"failed to decompress state_blob: {exc}") from exc

        pw = await self._ensure_playwright()
        user_data_dir = Path(tempfile.mkdtemp(prefix="coral-session-"))
        # Serialize the pick-port → Chromium-bind handoff. Two concurrent open()
        # calls could otherwise race on the same free port between `bind(0)`
        # closing and Chromium starting.
        async with self._launch_lock:
            cdp_port = _pick_tcp_port()
            # Tag the child Chromium with our coral_home so a future daemon
            # restart can find and kill orphans from this run (spec §7.4).
            child_env: dict[str, str | float | bool] = {k: v for k, v in os.environ.items()}
            if self._coral_home is not None:
                child_env[CORAL_DAEMON_HOME_ENV] = str(self._coral_home)
            try:
                context = await pw.chromium.launch_persistent_context(
                    user_data_dir=str(user_data_dir),
                    headless=self._headless,
                    env=child_env,
                    args=[
                        f"--remote-debugging-port={cdp_port}",
                        "--remote-debugging-address=127.0.0.1",
                        "--disable-dev-shm-usage",
                        "--no-first-run",
                    ],
                )
            except Exception as exc:
                shutil.rmtree(user_data_dir, ignore_errors=True)
                diag.error("session.open.failed", reason="chromium_launch", session_id=session_id)
                raise SessionServerError(f"failed to launch chromium: {exc}") from exc

        try:
            await apply_state_blob(context, blob)
            engine = await self._build_engine(record.origin)
            await self._install_route_handler(
                context,
                session_id=session_id,
                agent_id=agent_id,
                engine=engine,
            )
            cdp_url = await asyncio.to_thread(_read_cdp_ws_url, cdp_port)
        except Exception:
            with contextlib.suppress(Exception):
                await context.close()
            shutil.rmtree(user_data_dir, ignore_errors=True)
            raise

        handle = str(uuid.uuid4())
        opened_at = int(time.time())
        expires_at = opened_at + self._max_duration_seconds
        session = OpenSession(
            handle=handle,
            session_id=session_id,
            agent_id=agent_id,
            purpose=purpose,
            origin=record.origin,
            cdp_url=cdp_url,
            opened_at=opened_at,
            expires_at=expires_at,
            context=context,
            user_data_dir=user_data_dir,
            engine=engine,
        )

        async with self._lock:
            self._handles[handle] = session
            session.timeout_task = asyncio.create_task(
                self._auto_close(handle, self._max_duration_seconds),
                name=f"coral-session-timeout-{handle[:8]}",
            )

        await self._audit(
            event_type="session.opened",
            detail={"purpose": purpose, "origin": record.origin, "headless": self._headless},
            session_id=session_id,
            agent_id=agent_id,
            origin=record.origin,
        )
        diag.info(
            "session.opened",
            handle=handle,
            session_id=session_id,
            agent_id=agent_id,
            origin=record.origin,
        )
        return session

    async def close(self, handle: str, *, reason: str = "agent_closed") -> None:
        """Tear down a single open session. Idempotent.

        Before closing the Chromium context, attempts a policy-gated cookie
        write-back (ADR-018). Write-back is best-effort — any failure is
        logged but never blocks the close path.
        """
        async with self._lock:
            session = self._handles.pop(handle, None)
        if session is None:
            return
        if session.timeout_task is not None and not session.timeout_task.done():
            session.timeout_task.cancel()
        writeback_counts: dict[str, int] | None = None
        try:
            writeback_counts = await self._writeback_state(session=session, reason=reason)
        except Exception as exc:  # never let writeback block close
            diag.warn(
                "session.writeback.unexpected_error",
                reason=repr(exc),
                session_id=session.session_id,
            )
        with contextlib.suppress(Exception):
            await session.context.close()
        shutil.rmtree(session.user_data_dir, ignore_errors=True)
        await self._audit(
            event_type="session.closed",
            detail={"reason": reason, "duration_s": int(time.time()) - session.opened_at},
            session_id=session.session_id,
            agent_id=session.agent_id,
            origin=session.origin,
        )
        if writeback_counts is not None:
            await self._audit(
                event_type="session.state_written_back",
                detail=writeback_counts,
                session_id=session.session_id,
                agent_id=session.agent_id,
                origin=session.origin,
            )
        diag.info("session.closed", handle=handle, reason=reason)

    async def _writeback_state(
        self,
        *,
        session: OpenSession,
        reason: str,
    ) -> dict[str, int] | None:
        """Diff the live cookie jar against the captured blob and persist the
        policy-admitted delta (ADR-018).

        Returns counts (``added``, ``updated``, ``dropped_by_policy``,
        ``unchanged``) when something was persisted, or ``None`` when the
        write-back was skipped (revoke / shutdown / nothing to persist /
        soft failure).
        """
        if reason in ("session_revoked", "daemon_shutdown"):
            return None

        allowed_paths = session.engine.policy.allowed_paths
        if not allowed_paths:
            return None

        try:
            live_cookies = await session.context.cookies()
        except Exception as exc:
            diag.warn(
                "session.writeback.cookies_unavailable",
                reason=repr(exc),
                session_id=session.session_id,
            )
            return None

        record = await self._vault.get_session(session.session_id)
        if record is None or record.status != "active":
            return None
        try:
            original_blob = decompress_blob(record.state_blob)
        except Exception as exc:
            diag.warn(
                "session.writeback.decompress_failed",
                reason=repr(exc),
                session_id=session.session_id,
            )
            return None

        original_raw: Any = original_blob.get("cookies") or []
        if not isinstance(original_raw, list):
            return None
        raw_list: list[Any] = cast(list[Any], original_raw)
        original_cookies: list[dict[str, Any]] = [
            cast(dict[str, Any], c) for c in raw_list if isinstance(c, dict)
        ]
        original_by_key: dict[tuple[str, str, str], dict[str, Any]] = {
            _cookie_key(c): c for c in original_cookies
        }

        counts: dict[str, int] = {
            "unchanged": 0,
            "updated": 0,
            "added": 0,
            "dropped_by_policy": 0,
        }
        merged: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str, str]] = set()

        for live_raw in live_cookies:
            live: dict[str, Any] = dict(live_raw)
            key = _cookie_key(live)
            seen_keys.add(key)
            cookie_path = str(live.get("path", "/"))
            if not _cookie_path_allowed(cookie_path, allowed_paths):
                counts["dropped_by_policy"] += 1
                if key in original_by_key:
                    merged.append(original_by_key[key])
                continue
            if key in original_by_key:
                if _cookies_equal(original_by_key[key], live):
                    counts["unchanged"] += 1
                else:
                    counts["updated"] += 1
            else:
                counts["added"] += 1
            merged.append(live)

        # Preserve originals not seen in the live jar (server-side expiry vs.
        # agent-side delete is indistinguishable from here — conservative
        # default is "keep the original"; documented in ADR-018).
        for key, cookie in original_by_key.items():
            if key not in seen_keys:
                merged.append(cookie)

        if not (counts["added"] or counts["updated"]):
            # Nothing to persist. Skip the round-trip; don't even audit.
            return None

        new_blob = dict(original_blob)
        new_blob["cookies"] = merged
        try:
            compressed = compress_blob(new_blob)
        except Exception as exc:
            diag.warn(
                "session.writeback.compress_failed",
                reason=repr(exc),
                session_id=session.session_id,
            )
            return None

        try:
            await self._vault.update_session_state_blob(session.session_id, compressed)
        except Exception as exc:
            diag.warn(
                "session.writeback.vault_write_failed",
                reason=repr(exc),
                session_id=session.session_id,
            )
            return None

        diag.info(
            "session.state_written_back",
            session_id=session.session_id,
            **counts,
        )
        return counts

    async def shutdown(self) -> None:
        """Close every open session (called on daemon shutdown)."""
        async with self._lock:
            handles = list(self._handles.keys())
        for handle in handles:
            await self.close(handle, reason="daemon_shutdown")
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    def list_handles(self) -> list[OpenSession]:
        return list(self._handles.values())

    def get(self, handle: str) -> OpenSession:
        if handle not in self._handles:
            raise SessionHandleNotFoundError(handle)
        return self._handles[handle]

    def engine_for_handle(self, handle: str) -> PolicyEngine:
        return self.get(handle).engine

    async def _auto_close(self, handle: str, delay_seconds: int) -> None:
        try:
            await asyncio.sleep(delay_seconds)
        except asyncio.CancelledError:
            return
        await self.close(handle, reason="timeout")

    async def _build_engine(self, origin: str) -> PolicyEngine:
        """Load the persisted policy for ``origin`` or fall back to the default."""
        record = await self._vault.get_policy(origin)
        if record is None:
            return PolicyEngine(default_policy_for_origin(origin))
        try:
            policy = load_policy_yaml(origin=origin, yaml_body=record.yaml_body)
        except Exception as exc:
            diag.warn("policy.load_failed", origin=origin, reason=repr(exc))
            return PolicyEngine(default_policy_for_origin(origin))
        return PolicyEngine(policy)

    async def _install_route_handler(
        self,
        context: BrowserContext,
        *,
        session_id: str,
        agent_id: str,
        engine: PolicyEngine,
    ) -> None:
        """Route every request through the policy engine (Track E)."""

        async def _route_handler(route: Route) -> None:
            req = route.request
            if not req.is_navigation_request():
                await route.continue_()
                return
            decision = engine.evaluate_navigation(req.url)
            base_url = req.url.split("?", 1)[0]
            if decision == "allow":
                await self._audit(
                    event_type="navigation",
                    detail={"url": req.url, "method": req.method, "decision": "allow"},
                    session_id=session_id,
                    agent_id=agent_id,
                    origin=base_url,
                )
                await route.continue_()
                return
            # deny + review_required both abort the request.
            await self._audit(
                event_type="policy.deny" if decision == "deny" else "policy.review_required",
                detail={"url": req.url, "method": req.method, "decision": decision},
                session_id=session_id,
                agent_id=agent_id,
                origin=base_url,
            )
            await route.abort("blockedbyclient")

        await context.route("**/*", _route_handler)

    async def _audit(
        self,
        *,
        event_type: str,
        detail: dict[str, Any],
        session_id: str | None = None,
        agent_id: str | None = None,
        origin: str | None = None,
    ) -> None:
        from coral.audit import write_audit_row

        await write_audit_row(
            self._vault,
            event_type=event_type,
            detail=detail,
            session_id=session_id,
            agent_id=agent_id,
            origin=origin,
        )


_CHROMIUM_PROCESS_NAMES = frozenset(
    {
        "chrome",
        "chromium",
        "chromium-browser",
        "google-chrome",
        "Google Chrome",
        "Google Chrome Helper",
        "chrome-headless-shell",
        "chrome-mac",
        "chrome.exe",
    }
)


def recovery_kill_orphan_browsers(coral_home: Path) -> int:
    """Best-effort cleanup of Chromium processes from a crashed previous daemon (§7.4).

    Identifies survivors by matching the ``CORAL_DAEMON_HOME`` env var that
    :class:`SessionServer.open` injects into every child Chromium. Only processes
    tagged with *our* coral_home are killed — leaves other Coral daemons (running
    against different homes) and unrelated browsers alone.

    Returns the number of processes killed. Safe to call when the current daemon
    has nothing open (returns 0 in the common case).
    """
    import psutil

    target = str(coral_home)
    killed = 0
    for proc in psutil.process_iter(attrs=["pid", "name"]):
        try:
            name = proc.info.get("name") or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name not in _CHROMIUM_PROCESS_NAMES:
            continue
        try:
            env = proc.environ()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if env.get(CORAL_DAEMON_HOME_ENV) != target:
            continue
        try:
            proc.kill()
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return killed
