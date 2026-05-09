"""MCP server scaffold tests (spec §3.3, §5.2)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import httpx
import pytest
from starlette.testclient import TestClient

from coral.crypto import TEST_PARAMS, generate_token, hash_token
from coral.mcp_server import (
    MCPRuntime,
    _coral_list_sessions,
    _not_implemented,
    build_authed_mcp_http_app,
    build_mcp_server,
    set_runtime,
)
from coral.vault import Vault, make_demo_session_record


@pytest.fixture
async def runtime_vault(
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncIterator[Vault]:
    home = tmp_path_factory.mktemp("mcp_vault")
    vault = await Vault.initialize(home, "correct horse battery staple", params=TEST_PARAMS)
    set_runtime(MCPRuntime(vault=vault, agent_name="pytest"))
    try:
        yield vault
    finally:
        set_runtime(None)
        await vault.close()


def test_mcp_server_registers_five_tools() -> None:
    mcp = build_mcp_server()

    async def names() -> list[str]:
        tools = await mcp.list_tools()
        return sorted(t.name for t in tools)

    import asyncio

    found = asyncio.run(names())
    assert found == sorted(
        [
            "coral_list_sessions",
            "coral_open_session",
            "coral_close_session",
            "coral_check_action",
            "coral_request_review",
        ]
    )


async def test_list_sessions_with_no_sessions(runtime_vault: Vault) -> None:
    res = await _coral_list_sessions()
    assert res == {"sessions": []}


async def test_list_sessions_returns_active_only(runtime_vault: Vault) -> None:
    rec = make_demo_session_record(origin="https://example.com")
    await runtime_vault.insert_session(rec)
    res = await _coral_list_sessions()
    assert len(res["sessions"]) == 1
    assert res["sessions"][0]["origin"] == "https://example.com"

    await runtime_vault.revoke_session(rec.id)
    res2 = await _coral_list_sessions()
    assert res2 == {"sessions": []}


async def test_unimplemented_tool_raises_with_clear_message(runtime_vault: Vault) -> None:
    stub = _not_implemented("coral_open_session")
    with pytest.raises(NotImplementedError) as exc:
        await stub(arguments={"session_id": "x"})
    assert "week 2" in str(exc.value)
    assert "coral_open_session" in str(exc.value)


async def test_authed_mcp_http_rejects_unauthed(runtime_vault: Vault) -> None:
    mcp = build_mcp_server()
    app = build_authed_mcp_http_app(mcp, vault=runtime_vault)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
    assert r.status_code == 401
    assert r.json()["error"] == "missing_authorization"


async def test_authed_mcp_http_rejects_wrong_scheme(runtime_vault: Vault) -> None:
    mcp = build_mcp_server()
    app = build_authed_mcp_http_app(mcp, vault=runtime_vault)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Basic abc",
            },
        )
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_authorization_scheme"


async def test_authed_mcp_http_rejects_expired_token(runtime_vault: Vault) -> None:
    raw = generate_token()
    await runtime_vault.insert_token(
        hash_token(raw), name="expired", expires_at=int(time.time()) - 60
    )
    mcp = build_mcp_server()
    app = build_authed_mcp_http_app(mcp, vault=runtime_vault)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {raw}",
            },
        )
    assert r.status_code == 401
    assert r.json()["error"] == "token_expired"
    rows = await runtime_vault.query_audit(since=None, limit=10)
    assert any(
        r.event_type == "auth.failed" and "token_expired" in r.detail and "mcp-http" in r.detail
        for r in rows
    )


async def test_authed_mcp_http_rejects_unknown_token(runtime_vault: Vault) -> None:
    mcp = build_mcp_server()
    app = build_authed_mcp_http_app(mcp, vault=runtime_vault)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        r = await c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {generate_token()}",
            },
        )
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_token"
    rows = await runtime_vault.query_audit(since=None, limit=10)
    assert any(r.event_type == "auth.failed" and "mcp-http" in r.detail for r in rows)


# Note: the success-path test for the MCP HTTP middleware is intentionally absent.
# FastMCP's streamable_http_app needs Starlette's lifespan to initialize its session
# manager (TestClient handles that), but the bearer middleware needs an awaitable
# vault — and the vault's writer task is bound to whichever event loop opened it.
# Mixing a sync TestClient (which runs requests on a portal loop) with an async-loop-
# bound vault is the same cross-loop deadlock that caused the original test_auth.py
# hang. The middleware's success branch is a single ``await call_next(request)`` with
# no Coral-specific logic; the auth-rejection paths above + the stdio integration
# test (which exercises the same FastMCP) cover everything material.


def test_mcp_streamable_http_initialize() -> None:
    mcp = build_mcp_server()
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "coral-pytest", "version": "0.0.0"},
        },
    }
    with TestClient(mcp.streamable_http_app()) as client:
        resp = client.post(
            "/mcp",
            json=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"]["serverInfo"]["name"] == "coral"
