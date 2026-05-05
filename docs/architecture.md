# Architecture (stub)

Expand this document alongside implementation. The authoritative overview lives in
[`coral-engineering-spec.md`](../coral-engineering-spec.md) §3.

## Process model

Single Python daemon: FastAPI (extension/CLI), MCP (stdio + streamable HTTP), encrypted vault,
and (later) Playwright session server share one asyncio loop.

## Networking

- HTTP API: `127.0.0.1:8765` by default
- MCP streamable HTTP: `127.0.0.1:8766` by default
- MCP stdio: `coral mcp-stdio` subprocess entry for agent hosts that spawn Coral
