"""MCP integration (spec §3.3, §5.2).

Tools registered (week 1):

- ``coral_list_sessions`` — implemented; returns active sessions for the agent.
- ``coral_open_session``, ``coral_close_session``, ``coral_check_action``,
  ``coral_request_review`` — registered with schemas; raise NotImplementedError
  with a clear "implemented in week 2" message (per Track B prompt §6).

Agent identity for audit comes from the MCP ``initialize`` ``clientInfo.name``.
The daemon stores the active client name on a module-level handle when the MCP
session is established; tool handlers thread it into ``audit_log.agent_id``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from coral.models import AuditEntry
from coral.vault import Vault

WEEK2_MESSAGE: str = (
    "{tool} is registered but not yet implemented; the implementation lands in "
    "Coral week 2 (Playwright integration). Track B (week 1) ships the MCP "
    "scaffold and coral_list_sessions only."
)


@dataclass
class MCPRuntime:
    """Daemon-owned handle plumbed into FastMCP tool handlers."""

    vault: Vault
    agent_name: str = "mcp-client"


_runtime_state: MCPRuntime | None = None


def set_runtime(runtime: MCPRuntime | None) -> None:
    global _runtime_state
    _runtime_state = runtime


def _runtime() -> MCPRuntime:
    if _runtime_state is None:
        raise RuntimeError(
            "MCP runtime is not configured; the daemon must call set_runtime() before "
            "FastMCP starts serving."
        )
    return _runtime_state


async def _audit(
    *,
    event_type: str,
    detail: dict[str, Any],
    session_id: str | None = None,
    origin: str | None = None,
) -> None:
    rt = _runtime()
    entry = AuditEntry(
        timestamp=int(time.time()),
        session_id=session_id,
        agent_id=rt.agent_name,
        event_type=event_type,
        origin=origin,
        detail=json.dumps(detail, separators=(",", ":"), sort_keys=True),
    )
    await rt.vault.insert_audit(entry)


async def _coral_list_sessions() -> dict[str, Any]:
    """List active sessions visible to MCP clients (spec §5.2).

    Week-1 behavior: returns every ``status='active'`` session. Per-agent policy
    filtering is added when the policy engine ships in week 3.
    """
    rt = _runtime()
    rows = await rt.vault.list_sessions()
    sessions = [
        {
            "session_id": r.id,
            "origin": r.origin,
            "label": r.label,
            "created_at": r.created_at,
            "last_used_at": r.last_used_at,
            "expires_at": r.expires_at,
        }
        for r in rows
        if r.status == "active"
    ]
    await _audit(event_type="mcp.list_sessions", detail={"count": len(sessions)})
    return {"sessions": sessions}


def _not_implemented(tool_name: str) -> Callable[..., Awaitable[dict[str, Any]]]:
    async def _stub(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        del arguments
        await _audit(event_type="mcp.tool_called", detail={"tool_name": tool_name})
        raise NotImplementedError(WEEK2_MESSAGE.format(tool=tool_name))

    _stub.__name__ = tool_name
    return _stub


def register_tools(mcp: FastMCP) -> None:
    """Attach every Coral MCP tool to ``mcp``. Idempotent."""
    mcp.add_tool(
        _coral_list_sessions,
        name="coral_list_sessions",
        description=(
            "List authenticated browser sessions available to this agent. "
            "Week-1 returns all active sessions; per-agent policy filtering "
            "ships with the policy engine in week 3."
        ),
    )
    mcp.add_tool(
        _not_implemented("coral_open_session"),
        name="coral_open_session",
        description=(
            "Open an authenticated browser context restored from a captured session. "
            "Returns a CDP URL the agent can drive. Implementation in week 2."
        ),
    )
    mcp.add_tool(
        _not_implemented("coral_close_session"),
        name="coral_close_session",
        description="Close an open session context. Implementation in week 2.",
    )
    mcp.add_tool(
        _not_implemented("coral_check_action"),
        name="coral_check_action",
        description=(
            "Evaluate whether an action is allowed under the session's policy. "
            "Implementation in week 2 (engine in week 3)."
        ),
    )
    mcp.add_tool(
        _not_implemented("coral_request_review"),
        name="coral_request_review",
        description=(
            "Request operator review for a policy-flagged action. Implementation in "
            "week 2 (review UX in week 3)."
        ),
    )


def build_mcp_server(*, http_host: str = "127.0.0.1", http_port: int = 8766) -> FastMCP:
    """Construct the Coral MCP server with all tools registered.

    The MCP HTTP transport binds to ``127.0.0.1`` only (spec §6.2 T2). The stdio
    transport is started by ``coral mcp-stdio`` for subprocess-spawning clients.
    """
    mcp = FastMCP(
        "coral",
        instructions=(
            "Coral is a local-first browser session bridge. Week-1 surface: "
            "coral_list_sessions reads from the vault. Other tools are registered "
            "but raise NotImplementedError until week 2."
        ),
        host=http_host,
        port=http_port,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    register_tools(mcp)
    return mcp


async def run_mcp_stdio(*, vault: Vault, agent_name: str = "stdio") -> None:
    """Run Coral MCP with stdio transport.

    The caller provides a vault that the tools will read/write through. This is
    the same vault the daemon uses when ``coral mcp-stdio`` is launched in-process.
    """
    set_runtime(MCPRuntime(vault=vault, agent_name=agent_name))
    try:
        mcp = build_mcp_server()
        await mcp.run_stdio_async()
    finally:
        set_runtime(None)
