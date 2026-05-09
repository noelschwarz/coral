"""SessionServer integration tests (Track D).

Boots a Playwright Chromium per session against the local test-server fixture,
asserts the agent-facing CDP URL is reachable and the captured cookie carries
authentication, then closes cleanly.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator

import pytest

from coral.crypto import TEST_PARAMS
from coral.models import SessionRecord
from coral.sessions import (
    SessionNotActiveError,
    SessionNotFoundError,
    SessionServer,
)
from coral.vault import Vault, _compress_blob
from tests.fixtures.test_server import COOKIE_NAME, COOKIE_VALUE
from tests.fixtures.test_server.runner import run_test_server

try:  # noqa: SIM105
    import playwright  # noqa: F401
except ImportError:  # pragma: no cover
    pytest.skip("playwright not installed", allow_module_level=True)


def _build_session_for(origin: str) -> SessionRecord:
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
            "local_storage": {},
            "session_storage": {},
        }
    )
    return SessionRecord(
        id=str(uuid.uuid4()),
        origin=origin,
        label="test",
        created_at=int(time.time()),
        last_used_at=None,
        expires_at=None,
        status="active",
        state_blob=blob,
        metadata="{}",
    )


@pytest.fixture
async def vault(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Vault]:
    home = tmp_path_factory.mktemp("session_vault")
    v = await Vault.initialize(home, "correct horse battery staple", params=TEST_PARAMS)
    try:
        yield v
    finally:
        await v.close()


@pytest.fixture
async def server(vault: Vault) -> AsyncIterator[SessionServer]:
    s = SessionServer(vault=vault, max_duration_minutes=60, headless=True)
    try:
        yield s
    finally:
        await s.shutdown()


async def test_open_close_roundtrip(vault: Vault, server: SessionServer) -> None:
    with run_test_server() as base_url:
        rec = _build_session_for(base_url)
        await vault.insert_session(rec)

        opened = await server.open(session_id=rec.id, agent_id="pytest", purpose="hello")
        assert opened.cdp_url.startswith("ws://127.0.0.1:")
        assert opened.expires_at > opened.opened_at
        assert opened.handle in {h.handle for h in server.list_handles()}

        await server.close(opened.handle)
        assert opened.handle not in {h.handle for h in server.list_handles()}


async def test_open_carries_cookie_into_context(vault: Vault, server: SessionServer) -> None:
    """The captured cookie must reach the restored context end-to-end."""
    with run_test_server() as base_url:
        rec = _build_session_for(base_url)
        await vault.insert_session(rec)

        opened = await server.open(session_id=rec.id, agent_id="pytest", purpose="probe")
        try:
            page = await opened.context.new_page()
            response = await page.goto(f"{base_url}/me")
            assert response is not None and response.status == 200
            body = await response.json()
            assert body["cookie_seen"] == COOKIE_VALUE
        finally:
            await server.close(opened.handle)


async def test_open_unknown_session_id(vault: Vault, server: SessionServer) -> None:
    with pytest.raises(SessionNotFoundError):
        await server.open(session_id="does-not-exist", agent_id="pytest", purpose="x")


async def test_open_revoked_session_rejected(vault: Vault, server: SessionServer) -> None:
    rec = _build_session_for("http://127.0.0.1:1")
    await vault.insert_session(rec)
    await vault.revoke_session(rec.id)
    with pytest.raises(SessionNotActiveError):
        await server.open(session_id=rec.id, agent_id="pytest", purpose="x")


async def test_close_unknown_handle_is_idempotent(server: SessionServer) -> None:
    await server.close("not-a-handle")  # must not raise


async def test_audit_rows_recorded(vault: Vault, server: SessionServer) -> None:
    with run_test_server() as base_url:
        rec = _build_session_for(base_url)
        await vault.insert_session(rec)
        opened = await server.open(session_id=rec.id, agent_id="pytest", purpose="audit-probe")
        await server.close(opened.handle)

    rows = await vault.query_audit(since=None, limit=20)
    types = [r.event_type for r in rows]
    assert "session.opened" in types
    assert "session.closed" in types
