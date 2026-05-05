"""MCP integration (spec §5.2).

``FastMCP`` implements the Coral MCP server. Tool implementations are
intentionally deferred past repository foundations.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings


def build_mcp_server() -> FastMCP:
    """Construct the Coral MCP server without registering tools (foundations)."""
    return FastMCP(
        "coral",
        instructions=(
            "Coral is a local-first browser session bridge. "
            "Foundations release: MCP tools are stubbed; do not rely on tool calls yet."
        ),
        host="127.0.0.1",
        port=8766,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


async def run_mcp_stdio() -> None:
    """Run Coral MCP with stdio transport (subprocess-friendly agents)."""
    mcp = build_mcp_server()
    await mcp.run_stdio_async()
