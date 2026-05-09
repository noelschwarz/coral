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
import shutil
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from coral import diag
from coral.models import AuditEntry
from coral.restoration import apply_state_blob
from coral.vault import Vault, _decompress_blob

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
    timeout_task: asyncio.Task[None] | None = field(default=None, repr=False)

    def to_open_response(self) -> dict[str, Any]:
        """The §5.2 ``coral_open_session`` response shape."""
        return {
            "session_handle": self.handle,
            "cdp_url": self.cdp_url,
            "expires_at": self.expires_at,
            "policy_summary": {},  # week 3 fills this in
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


class SessionServer:
    """Owns every open Playwright session for the daemon's lifetime."""

    def __init__(
        self,
        *,
        vault: Vault,
        max_duration_minutes: int,
        headless: bool = True,
    ) -> None:
        self._vault = vault
        self._max_duration_seconds = max_duration_minutes * 60
        self._headless = headless
        self._handles: dict[str, OpenSession] = {}
        self._lock = asyncio.Lock()
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
            blob = _decompress_blob(record.state_blob)
        except Exception as exc:
            diag.error("session.open.failed", reason="decompress_failed", session_id=session_id)
            raise SessionServerError(f"failed to decompress state_blob: {exc}") from exc

        pw = await self._ensure_playwright()
        cdp_port = _pick_tcp_port()
        user_data_dir = Path(tempfile.mkdtemp(prefix="coral-session-"))
        try:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                headless=self._headless,
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
            await self._install_route_handler(
                context,
                session_id=session_id,
                agent_id=agent_id,
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
        """Tear down a single open session. Idempotent."""
        async with self._lock:
            session = self._handles.pop(handle, None)
        if session is None:
            return
        if session.timeout_task is not None and not session.timeout_task.done():
            session.timeout_task.cancel()
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
        diag.info("session.closed", handle=handle, reason=reason)

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

    async def _auto_close(self, handle: str, delay_seconds: int) -> None:
        try:
            await asyncio.sleep(delay_seconds)
        except asyncio.CancelledError:
            return
        await self.close(handle, reason="timeout")

    async def _install_route_handler(
        self,
        context: BrowserContext,
        *,
        session_id: str,
        agent_id: str,
    ) -> None:
        """Audit every navigation; week 3's policy engine replaces the body here."""

        async def _route_handler(route: Route) -> None:
            req = route.request
            if req.is_navigation_request():
                await self._audit(
                    event_type="navigation",
                    detail={"url": req.url, "method": req.method},
                    session_id=session_id,
                    agent_id=agent_id,
                    origin=req.url.split("?", 1)[0],
                )
            await route.continue_()

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
        entry = AuditEntry(
            timestamp=int(time.time()),
            session_id=session_id,
            agent_id=agent_id,
            event_type=event_type,
            origin=origin,
            detail=json.dumps(detail, separators=(",", ":"), sort_keys=True),
        )
        await self._vault.insert_audit(entry)


def recovery_kill_orphan_browsers() -> int:
    """Best-effort cleanup hook (spec §7.4). Implementation lands in week 4 polish."""
    return 0
