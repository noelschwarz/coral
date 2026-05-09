"""Coral's headline e2e flow (spec §13 success criterion).

  capture session against test server
  → MCP coral_open_session
  → connect agent-side Playwright to the returned CDP URL
  → navigate to /me, verify authenticated response
  → MCP coral_close_session

This is the test that validates the central thesis: the agent gets a working
logged-in browser without ever seeing the password.
"""

from __future__ import annotations

import os
import sys

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from coral.crypto import TEST_PARAMS
from coral.models import SessionRecord
from coral.vault import Vault, _compress_blob
from tests.fixtures.test_server import COOKIE_NAME, COOKIE_VALUE
from tests.fixtures.test_server.runner import run_test_server

try:  # noqa: SIM105
    import playwright  # noqa: F401
except ImportError:  # pragma: no cover
    pytest.skip("playwright not installed", allow_module_level=True)


async def _seed_session(home, origin: str) -> str:
    vault = await Vault.initialize(home, "correct horse battery staple", params=TEST_PARAMS)
    try:
        blob = _compress_blob(
            {
                "version": 1,
                "origin": origin,
                "cookies": [
                    {
                        "name": COOKIE_NAME,
                        "value": COOKIE_VALUE,
                        "domain": "127.0.0.1",
                        "path": "/",
                        "httpOnly": True,
                    }
                ],
            }
        )
        import time as _t
        import uuid as _u

        rec = SessionRecord(
            id=str(_u.uuid4()),
            origin=origin,
            label="e2e",
            created_at=int(_t.time()),
            last_used_at=None,
            expires_at=None,
            status="active",
            state_blob=blob,
            metadata="{}",
        )
        await vault.insert_session(rec)
        return rec.id
    finally:
        await vault.close()


@pytest.mark.asyncio
async def test_e2e_capture_open_drive_close(tmp_path) -> None:
    passphrase = "correct horse battery staple"

    with run_test_server() as base_url:
        # 1. seed a captured session in the vault directly (the extension's job).
        session_id = await _seed_session(tmp_path, base_url)

        # 2. spawn `coral mcp-stdio` so an MCP client can drive the daemon's tools.
        env = os.environ.copy()
        env.update(
            {
                "CORAL_HOME": str(tmp_path),
                "CORAL_PASSPHRASE": passphrase,
                "PYTHONUNBUFFERED": "1",
            }
        )
        # ensure the subprocess can find the pre-installed chromium
        env.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")

        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "coral", "mcp-stdio", "--agent-name", "e2e-pytest"],
            env=env,
        )
        async with (
            stdio_client(server) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()

            # 3. coral_open_session returns a CDP URL.
            open_res = await session.call_tool(
                "coral_open_session",
                {"session_id": session_id, "purpose": "verify auth carried"},
            )
            assert not open_res.isError, open_res.content
            data = open_res.structuredContent
            assert data is not None
            cdp_url = data["cdp_url"]
            handle = data["session_handle"]
            assert cdp_url.startswith("ws://127.0.0.1:")

            # 4. agent connects to the CDP URL with its own Playwright client.
            from playwright.async_api import async_playwright

            async with async_playwright() as pw:
                browser = await pw.chromium.connect_over_cdp(cdp_url)
                try:
                    contexts = browser.contexts
                    assert contexts, "no contexts visible to the agent"
                    ctx = contexts[0]
                    page = await ctx.new_page()
                    response = await page.goto(f"{base_url}/me")
                    assert response is not None and response.status == 200
                    body = await response.json()
                    assert body["cookie_seen"] == COOKIE_VALUE
                finally:
                    # connect_over_cdp does not own the browser; just disconnect
                    await browser.close()

            # 5. coral_close_session tears it down.
            close_res = await session.call_tool("coral_close_session", {"session_handle": handle})
            assert not close_res.isError

    # 6. confirm the audit log captured the lifecycle.
    from coral.vault import unlock_vault

    v = await unlock_vault(home=tmp_path, passphrase=passphrase)
    try:
        rows = await v.query_audit(since=None, limit=50)
        types = [r.event_type for r in rows]
        for expected in ("session.opened", "session.closed", "navigation"):
            assert expected in types, f"missing {expected} in {types}"
    finally:
        await v.close()
