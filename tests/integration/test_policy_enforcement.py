"""Integration test: policy engine actually blocks navigation (Track E)."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator

import pytest

from coral.crypto import TEST_PARAMS
from coral.models import SessionRecord
from coral.sessions import SessionServer
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
        }
    )
    return SessionRecord(
        id=str(uuid.uuid4()),
        origin=origin,
        label="policy-test",
        created_at=int(time.time()),
        last_used_at=None,
        expires_at=None,
        status="active",
        state_blob=blob,
        metadata="{}",
    )


@pytest.fixture
async def vault(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Vault]:
    home = tmp_path_factory.mktemp("policy_vault")
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


async def test_policy_denies_blocked_path(vault: Vault, server: SessionServer) -> None:
    """A denied_paths entry on the policy must abort the request before it reaches the server."""
    with run_test_server() as base_url:
        rec = _build_session_for(base_url)
        await vault.insert_session(rec)
        await vault.upsert_policy(
            base_url,
            ("default_action: allow\ndenied_paths:\n  - /protected\n  - /protected/*\n"),
        )

        opened = await server.open(session_id=rec.id, agent_id="pytest", purpose="deny-test")
        try:
            page = await opened.context.new_page()
            # An allowed path returns 200 with the cookie carrying auth.
            ok = await page.goto(f"{base_url}/me")
            assert ok is not None and ok.status == 200
            # The denied path: aborting the route surfaces as a navigation error.
            from playwright.async_api import Error as PWError

            with pytest.raises(PWError, match="ERR_BLOCKED_BY_CLIENT"):
                await page.goto(f"{base_url}/protected", wait_until="commit")
        finally:
            await server.close(opened.handle)

    rows = await vault.query_audit(since=None, limit=50)
    types = [r.event_type for r in rows]
    assert "policy.deny" in types


async def test_policy_review_required_blocks_navigation(
    vault: Vault, server: SessionServer
) -> None:
    """Paths that resolve to review_required also abort (agent must request review)."""
    with run_test_server() as base_url:
        rec = _build_session_for(base_url)
        await vault.insert_session(rec)
        await vault.upsert_policy(
            base_url,
            (
                "default_action: deny\nallowed_paths:\n  - /me\n"
                # /protected matches neither allowed nor denied → default deny
            ),
        )
        opened = await server.open(
            session_id=rec.id, agent_id="pytest", purpose="default-deny-test"
        )
        try:
            page = await opened.context.new_page()
            ok = await page.goto(f"{base_url}/me")
            assert ok is not None and ok.status == 200
            from playwright.async_api import Error as PWError

            with pytest.raises(PWError, match="ERR_BLOCKED_BY_CLIENT"):
                await page.goto(f"{base_url}/protected", wait_until="commit")
        finally:
            await server.close(opened.handle)
