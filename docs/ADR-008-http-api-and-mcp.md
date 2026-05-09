# ADR-008: HTTP API surface and MCP transport

## Status

Accepted — Week 1 Track B (2026).

## Context

Track A landed the vault and crypto primitives. The daemon could open the vault and
expose `/healthz`, but no client could reach the data behind it. Track B is the
session that turns Coral into a real surface for the Chrome extension and for
MCP-speaking agents.

Open questions resolved during this session (Track B handoff note, items 1-5):

1. **Token expiry.** Extension tokens live 24h; the CLI bridge token lives 30 days.
   Both are configurable on `Config` for power users.
2. **CLI token storage.** `$CORAL_HOME/cli.token`, mode 0600, written at daemon
   startup, removed at shutdown. The plaintext is held only on disk; the vault
   stores the SHA-256 hash and the name `"cli"`. Rationale: the bridge process
   (`coral mcp-stdio`) cannot share the daemon's vault key without re-prompting
   for the passphrase; file permissions on `$CORAL_HOME` are the existing trust
   boundary (already in T1 of the threat model).
3. **MCP HTTP transport.** SSE via FastMCP's `streamable_http_app` — the
   canonical transport every existing MCP client expects.
4. **`coral_list_sessions` filter.** All `status='active'` sessions are returned
   for v1. Per-agent filtering arrives with the policy engine in week 3.
5. **Audit-write failure handling.** Fail loudly: HTTP 500 with body
   `{"error": "audit_log_write_failed", ...}`, log the underlying exception to
   stderr, no retry. Audit integrity is non-negotiable.

## Decision

### Authentication

- A single bearer-token scheme covers every authenticated route and the MCP HTTP
  transport (when an MCP client opts in). Tokens are minted by
  `POST /auth/handshake`; the daemon stores only the SHA-256 hash in `api_tokens`.
- The submitted token leaves the daemon process exactly once — in the handshake
  response. Subsequent requests carry it in `Authorization: Bearer <token>`.
- Authentication failures write `auth.failed` rows that record the *reason*
  (`token_not_found`, `token_expired`, etc.) and nothing reversible to a credential.
- The submitted *challenge* is single-use and constant-time compared; consuming it
  invalidates further handshake attempts until daemon restart.

### Bind address

- HTTP API: `127.0.0.1:8765`. MCP HTTP: `127.0.0.1:8766`. Both hardcoded — there is
  no configuration path that allows binding to any other interface. The daemon
  raises a `RuntimeError` if `Config.http_host` is anything other than `127.0.0.1`.

### CORS

- Allowlist regex: `^chrome-extension://[A-Za-z0-9_-]+$`. Localhost web origins
  (`http://localhost:3000`, etc.) are explicitly rejected; agents that don't run
  inside a browser don't need CORS at all.
- Allowed methods: `GET POST PUT DELETE OPTIONS`. Allowed headers: `Authorization`,
  `Content-Type`. `Access-Control-Allow-Origin: *` is never used.

### Response shapes

- `GET /sessions` returns a list of `SessionListItem` Pydantic models — explicitly
  *not* `SessionRecord`. `state_blob` and `metadata` are stripped because returning
  them over HTTP would let a leaked bearer token exfiltrate the captured cookies
  and storage. State blobs leave the daemon only into Playwright contexts the
  daemon owns (week 2).

### Rate limiting

- The handshake endpoint enforces 5 attempts per 60-second sliding window per
  process lifetime. Exceeded attempts return `429` and write
  `auth.handshake.rate_limited`. The counter resets on daemon restart.

### MCP scaffold

- All five tools (`coral_list_sessions`, `coral_open_session`, `coral_close_session`,
  `coral_check_action`, `coral_request_review`) are registered on the FastMCP
  instance with their schemas. Only `coral_list_sessions` runs; the other four
  raise `NotImplementedError` with a "implemented in week 2" message and write
  an `mcp.tool_called` audit row first.
- The vault is plumbed to MCP tool handlers via a module-level `MCPRuntime`
  handle set at daemon startup. Tools that mutate state always go through the
  vault's writer queue (no direct sqlite3 calls from MCP land).

### Stdio bridge

- `coral mcp-stdio` opens its own vault from the user's passphrase and runs
  FastMCP over stdio. SQLCipher tolerates concurrent readers on the same database
  file, so this can run alongside `coral start`. The fully-bridged variant
  described in the engineer prompt — proxying stdio over HTTP to a running
  daemon — was deferred per the handoff note's "if the bridge is harder than
  expected, fall back to duplication" guidance. The duplicated path is small
  and avoids inventing JSON-RPC plumbing.

## Consequences

- The Chrome extension can now complete a real handshake and capture sessions.
- MCP-speaking agents can list sessions over both stdio and HTTP transports.
- The audit log is now load-bearing: every authenticated call writes an event,
  enabling the §3.1 "every action audited" claim in the threat model.
- The CLI token written to `$CORAL_HOME/cli.token` is a new disk artifact; its
  trust boundary (read access to `$CORAL_HOME` ⇒ daemon access) is already
  documented under T1.

## Accepted limitations

- No token revocation surface (`DELETE /tokens/{id}`); tokens expire on their own.
  Adding revocation is straightforward and is queued post-v1.
- MCP HTTP transport runs without bearer auth on the SSE endpoint for v1. It is
  localhost-only (T2 covers binding); the extension does not use this transport.
  Agents wanting authenticated MCP HTTP should wait for v1.x.
- The handshake rate limiter is in-process and resets on daemon restart. A
  legitimate user retrying many times will hit the limit until they restart the
  daemon — that's the correct UX (forces a fresh challenge), but it's a sharp
  edge worth knowing about.

## When to revisit

- When a non-Chromium browser extension is supported, broaden the CORS regex.
- When MCP clients land that need authenticated HTTP transport, integrate the
  bearer middleware into FastMCP's HTTP app.
- When the policy engine ships, make `coral_list_sessions` filter by per-agent
  policy.
