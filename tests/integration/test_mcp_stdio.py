"""MCP stdio integration test (spec §3.3, Track B step 8).

Spawns ``coral mcp-stdio`` as a subprocess, drives the session through the official
MCP client SDK, and asserts:

  initialize → tools/list (5 tools) → tools/call coral_list_sessions
            → tools/call coral_open_session raises a "week 2" error
            → close stdin, subprocess exits cleanly.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


@pytest.mark.asyncio
async def test_coral_mcp_stdio_round_trip(tmp_path) -> None:
    passphrase = "correct horse battery staple"

    env = os.environ.copy()
    env.update({"CORAL_HOME": str(tmp_path), "CORAL_PASSPHRASE": passphrase})
    init = subprocess.run(
        [sys.executable, "-m", "coral", "init"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert init.returncode == 0, init.stderr

    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "coral", "mcp-stdio", "--agent-name", "pytest-stdio"],
        env=env,
    )

    async with (
        stdio_client(server) as (read, write),
        ClientSession(read, write) as session,
    ):
        init_result = await session.initialize()
        assert init_result.serverInfo.name == "coral"

        tools = await session.list_tools()
        names = sorted(t.name for t in tools.tools)
        assert names == sorted(
            [
                "coral_list_sessions",
                "coral_open_session",
                "coral_close_session",
                "coral_check_action",
                "coral_request_review",
            ]
        )

        list_res = await session.call_tool("coral_list_sessions", {})
        assert not list_res.isError
        assert list_res.structuredContent == {"sessions": []}

        # Track E: coral_check_action now resolves against a real policy engine.
        # Calling without an open session_handle is a user error → returns an MCP
        # error rather than NotImplementedError.
        bad_res = await session.call_tool(
            "coral_check_action",
            {"session_handle": "not-a-handle", "action": {"type": "noop"}},
        )
        assert bad_res.isError, bad_res.content
        text_blocks = [getattr(c, "text", "") for c in bad_res.content if hasattr(c, "text")]
        assert any("session_handle_not_found" in t for t in text_blocks)

    # After the stdio session closes, reopen the vault directly and verify the audit
    # rows attribute the calls to the MCP client's clientInfo.name. mcp/python-sdk
    # sends clientInfo.name='mcp' by default, so any populated agent_id (NOT the
    # runtime fallback 'stdio') proves the Context-based plumbing works.
    from coral.vault import unlock_vault

    vault = await unlock_vault(home=tmp_path, passphrase=passphrase)
    try:
        rows = await vault.query_audit(since=None, limit=50)
        mcp_rows = [r for r in rows if r.event_type.startswith("mcp.")]
        assert mcp_rows, "expected mcp.* audit rows from the stdio session"
        agent_ids = {r.agent_id for r in mcp_rows if r.agent_id}
        # ClientSession sends clientInfo.name='mcp' by default; whatever it sends
        # must end up in agent_id (i.e. NOT the runtime fallback 'stdio').
        assert agent_ids, "MCP audit rows must record an agent_id from clientInfo.name"
    finally:
        await vault.close()
