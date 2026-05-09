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

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from coral.crypto import hash_token
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


def _agent_from_ctx(ctx: Any) -> str:
    """Resolve the calling agent's name from MCP ``clientInfo.name`` when available.

    Falls back to the runtime's default ``agent_name`` (set at daemon startup or by
    ``coral mcp-stdio --agent-name``) when no client info is reachable yet.
    """
    rt = _runtime()
    if ctx is not None:
        try:
            name = ctx.session.client_params.clientInfo.name
            if name:
                return str(name)
        except (AttributeError, RuntimeError, ValueError):
            pass
    return rt.agent_name


async def _audit(
    *,
    event_type: str,
    detail: dict[str, Any],
    session_id: str | None = None,
    origin: str | None = None,
    agent_id: str | None = None,
) -> None:
    rt = _runtime()
    entry = AuditEntry(
        timestamp=int(time.time()),
        session_id=session_id,
        agent_id=agent_id or rt.agent_name,
        event_type=event_type,
        origin=origin,
        detail=json.dumps(detail, separators=(",", ":"), sort_keys=True),
    )
    await rt.vault.insert_audit(entry)


async def _coral_list_sessions(ctx: Context[Any, Any, Any] | None = None) -> dict[str, Any]:
    """List active sessions visible to MCP clients (spec §5.2).

    Week-1 behavior: returns every ``status='active'`` session. Per-agent policy
    filtering is added when the policy engine ships in week 3.
    """
    rt = _runtime()
    agent_id = _agent_from_ctx(ctx)
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
    await _audit(
        event_type="mcp.list_sessions",
        detail={"count": len(sessions)},
        agent_id=agent_id,
    )
    return {"sessions": sessions}


def _not_implemented(tool_name: str) -> Callable[..., Awaitable[dict[str, Any]]]:
    async def _stub(
        ctx: Context[Any, Any, Any] | None = None,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del arguments
        agent_id = _agent_from_ctx(ctx)
        await _audit(
            event_type="mcp.tool_called",
            detail={"tool_name": tool_name},
            agent_id=agent_id,
        )
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


class MCPBearerAuth(BaseHTTPMiddleware):
    """Bearer-token guard for FastMCP's HTTP transport (spec §5.2 / Track B step 6).

    Mirrors :func:`coral.auth.require_auth`: identical token validation against the
    vault's ``api_tokens`` table, identical 401 error shape, identical audit trail
    (``auth.failed`` rows recording the *reason* only). The middleware stores the
    resolved client name on ``request.state.coral_agent`` so the FastMCP layer can
    pick it up if needed.
    """

    def __init__(self, app: ASGIApp, *, vault: Vault) -> None:
        super().__init__(app)
        self._vault = vault

    async def _audit_failure(self, reason: str) -> None:
        entry = AuditEntry(
            timestamp=int(time.time()),
            session_id=None,
            agent_id=None,
            event_type="auth.failed",
            origin=None,
            detail=json.dumps(
                {"reason": reason, "transport": "mcp-http"},
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        await self._vault.insert_audit(entry)

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        header = request.headers.get("authorization") or request.headers.get("Authorization")
        if not header:
            return JSONResponse(
                status_code=401,
                content={"error": "missing_authorization"},
                headers={"WWW-Authenticate": 'Bearer realm="coral-mcp"'},
            )
        parts = header.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
            return JSONResponse(
                status_code=401,
                content={"error": "invalid_authorization_scheme"},
                headers={"WWW-Authenticate": 'Bearer realm="coral-mcp"'},
            )
        token = parts[1].strip()
        record = await self._vault.verify_token(token)
        if record is None:
            await self._audit_failure("token_not_found")
            return JSONResponse(
                status_code=401,
                content={"error": "invalid_token"},
                headers={"WWW-Authenticate": 'Bearer realm="coral-mcp"'},
            )
        now = int(time.time())
        if record.expires_at < now:
            await self._audit_failure("token_expired")
            return JSONResponse(
                status_code=401,
                content={"error": "token_expired"},
                headers={"WWW-Authenticate": 'Bearer realm="coral-mcp"'},
            )
        await self._vault.touch_token_last_used(hash_token(token), now)
        request.state.coral_agent = record.name
        return await call_next(request)


def build_authed_mcp_http_app(mcp: FastMCP, *, vault: Vault) -> ASGIApp:
    """Wrap ``mcp.streamable_http_app()`` with the Coral bearer-token middleware."""
    return MCPBearerAuth(mcp.streamable_http_app(), vault=vault)


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
