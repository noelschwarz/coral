"""MCP server scaffold tests (spec §3.3, §5.2)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from starlette.testclient import TestClient

from coral.crypto import TEST_PARAMS
from coral.mcp_server import (
    MCPRuntime,
    _coral_list_sessions,
    _not_implemented,
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
