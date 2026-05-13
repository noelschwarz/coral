# Coral — Architecture

Authoritative source: [`coral-engineering-spec.md`](../coral-engineering-spec.md) §3.
This page documents what was actually built, module by module, current as of
Track G (v0.5.0). When the spec and this page disagree, this page wins for
implementation details; the spec wins for intent.

## Process model

One Python daemon. One event loop. All subsystems are tasks on that loop —
no IPC between subsystems within the daemon.

```
┌──────────────────────────────────────────────────────────────────┐
│                      USER'S LOCAL MACHINE                        │
│                                                                  │
│  ┌──────────────────┐         ┌──────────────────────────────┐  │
│  │   Chrome         │         │      Coral Daemon (Python)   │  │
│  │   (real browser) │         │                              │  │
│  │  ┌─────────────┐ │   HTTP  │  ┌────────────────────────┐  │  │
│  │  │ Coral       │◄┼─────────┼──┤ FastAPI (127.0.0.1:8765)│ │  │
│  │  │ Extension*  │ │  :8765  │  │ + bearer-token middleware│ │  │
│  │  └─────────────┘ │         │  └────────────┬───────────┘  │  │
│  └──────────────────┘         │               │              │  │
│                               │  ┌────────────┴───────────┐  │  │
│  ┌──────────────────┐  MCP    │  │ Vault (SQLCipher)      │  │  │
│  │  AI Agent        │  stdio  │  │ + asyncio writer task  │  │  │
│  │  (Claude Code,   │◄────────┼──┤                        │  │  │
│  │  browser-use,    │  or     │  └────────────┬───────────┘  │  │
│  │  Stagehand, ...) │  HTTP   │               │              │  │
│  └──────────────────┘  :8766  │  ┌────────────┴───────────┐  │  │
│                               │  │ SessionServer          │  │  │
│  ┌──────────────────┐         │  │ (Playwright per session)│ │  │
│  │  Isolated        │◄────────┼──┤ + route handler with    │ │  │
│  │  Chromium        │  CDP    │  │ policy enforcement     │  │  │
│  │  (per session)   │         │  └────────────┬───────────┘  │  │
│  └──────────────────┘         │               │              │  │
│                               │  ┌────────────┴───────────┐  │  │
│                               │  │ PolicyEngine + Audit   │  │  │
│                               │  └────────────────────────┘  │  │
│                               └──────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘

* Extension is a separate codebase in /extension/ (skeleton at this stage).
```

## Module map

### Core data

| Module | Responsibility |
|---|---|
| `coral/vault.py` | Encrypted SQLCipher store. Async façade over a sync connection via a single-thread `ThreadPoolExecutor`. Single writer task on the daemon loop serializes mutations. Schema migrations live in `coral/migrations/*.sql`. |
| `coral/models.py` | Pydantic row models: `SessionRecord`, `TokenRecord`, `AuditEntry`, `PolicyRecord`, `ReviewRecord`. |
| `coral/crypto.py` | Argon2id key derivation, secrets-based token generation, SHA-256 token hashing, constant-time comparison, handshake challenge generation. |
| `coral/config.py` | Pydantic `Config` model. Loads `~/.coral/config.toml`; respects `CORAL_HOME`, `CORAL_HTTP_*`, `CORAL_PASSPHRASE`. |
| `coral/paths.py` | Filesystem layout (`$CORAL_HOME/{vault.db, vault_meta.json, cli.token, coral.pid}`). |

### Boundary layers

| Module | Responsibility |
|---|---|
| `coral/http_api.py` | FastAPI app. §5.1 endpoints + `/auth/refresh` + `/tokens` CRUD + `/reviews` decision. CORS regex pinned to `chrome-extension://*`. Single-use challenge + 5/min rate limit (`HandshakeState`). Audit-write failure → HTTP 500. |
| `coral/auth.py` | Bearer-token middleware. Identical 401 shape for missing / wrong scheme / unknown / expired. Touches `last_used_at` on success. |
| `coral/mcp_server.py` | FastMCP scaffold + tool registration + `MCPBearerAuth` middleware for the SSE transport. Five tools (`coral_list_sessions`, `coral_open_session`, `coral_close_session`, `coral_check_action`, `coral_request_review`). |
| `coral/api_models.py` | Request/response Pydantic models. `SessionListItem` deliberately excludes `state_blob` (no credential exfiltration via leaked token). |

### Behavior

| Module | Responsibility |
|---|---|
| `coral/sessions.py` | `SessionServer` — per-session Chromium ([ADR-010](ADR-010-per-session-chromium.md)) via `launch_persistent_context`. Owns the route handler that runs the policy engine and audits every navigation. Max-duration auto-close. Orphan-process recovery on daemon startup ([§7.4](../coral-engineering-spec.md)). |
| `coral/restoration.py` | Translates the captured `state_blob` → Playwright cookies + init script seeding `localStorage`/`sessionStorage`. IDB and service workers deferred per spec §6.4. |
| `coral/policy.py` | YAML loader (Pydantic, `extra: forbid`), `PolicyEngine` (allow/deny/review_required), sliding-window rate limiter. Bundled packs in `coral/behavior_packs/`. |
| `coral/audit.py` | Single canonical audit-write path. Every audit row in the codebase flows through `write_audit_row`. |
| `coral/diag.py` | Structured stderr JSON logging, disjoint from the audit log. Filtered by `CORAL_DIAG_LEVEL`. |

### Orchestration

| Module | Responsibility |
|---|---|
| `coral/daemon.py` | Startup sequence: orphan sweep → unlock vault → provision `cli.token` → seed bundled packs → mint challenge → write PID file → register signal handlers → start FastAPI + MCP HTTP. Graceful shutdown closes everything in reverse. |
| `coral/cli.py` | Typer entry point. `init`, `start`, `stop`, `status`, `list`, `revoke`, `audit`, `panic`, `policy {get,put}`, `reviews list`, `approve`, `deny`, `mcp-stdio`. |
| `coral/cli_client.py` | Synchronous HTTP-API client used by the CLI. Unit-testable without subprocess gymnastics. |

## Data flow: capture → restore

1. Extension reads cookies + storage for the active tab's origin.
2. Extension POSTs `/sessions` with the captured state.
3. Daemon validates origin, gzips the JSON, writes a `sessions` row (status `active`).
4. Agent calls MCP `coral_open_session` with the `session_id`.
5. `SessionServer` reads the row, decompresses the blob, launches a fresh Chromium with `--remote-debugging-port=0` + `CORAL_DAEMON_HOME` env tag.
6. `apply_state_blob` adds cookies + registers an init script seeding storage.
7. The route handler is installed on the context; every navigation passes through `PolicyEngine.evaluate_navigation`.
8. CDP WS URL is read from `/json/version`, returned to the agent in the MCP response.
9. Agent connects via any CDP client (Playwright, Puppeteer, raw CDP) and drives the browser.
10. On agent close / max-duration timeout / daemon SIGTERM: context + Chromium close cleanly; `session.closed` audit row written.

## Trust boundaries

| Boundary | Defense |
|---|---|
| Network adversary | Out of scope. Daemon binds `127.0.0.1` only. |
| Other local processes | Cannot read the vault without the passphrase (Argon2id + SQLCipher). Cannot impersonate the daemon (bearer tokens required, generated per process via single-use challenge). |
| Browser extensions | CORS regex restricts daemon to `chrome-extension://*`. Bearer token never leaves Coral's own extension. |
| Malicious agent with CDP control | **Accepted risk.** Agent trust is your problem; Coral bounds blast radius via per-session Chromium + policy + audit. Spec §6.2 T6. |
| Disk-level attacker | Vault file is opaque without the passphrase. Plaintext `vault_meta.json` leaks only the salt + Argon2 params (not secret). |

See [`THREAT_MODEL.md`](../THREAT_MODEL.md) for the full T1-T11 walk-through.

## Why these choices (pointers to ADRs)

- **SQLCipher over application-layer AES** — [ADR-006](ADR-006-vault-encryption.md). AES fallback exists on paper, ships if SQLCipher wheel availability breaks.
- **PID file at `$CORAL_HOME/coral.pid`** — [ADR-007](ADR-007-daemon-lifecycle.md). Cross-platform consistency over Linux-conventional `$XDG_RUNTIME_DIR`.
- **HTTP API + MCP scaffold separated** — [ADR-008](ADR-008-http-api-and-mcp.md). MCP SDK rough edges may force re-architecture; HTTP stays stable.
- **Refresh / panic / status / diag** — [ADR-009](ADR-009-daily-use-ux.md). Daily-use UX shipped early, not waiting for v1.1.
- **One Chromium per session** — [ADR-010](ADR-010-per-session-chromium.md). Sound isolation over shared-Chromium memory savings until a CDP target filter exists.
- **Default-allow honored, deny shipped via packs** — [ADR-011](ADR-011-policy-engine.md). Don't unilaterally flip the spec default; pick the safe posture in the packs instead.
- **Five items deferred post-cleanup** — [ADR-012](ADR-012-deferred-cleanup.md). Vault writer cross-loop, AES fallback, notifications, shared-Chromium-CDP-filter, subprocess coverage.
- **Release tooling deferred until extension** — [ADR-013](ADR-013-release-strategy.md).

## What this page intentionally doesn't cover

- The Chrome extension's internal architecture (it's a separate codebase).
- The MCP protocol details (canonical: <https://modelcontextprotocol.io/>).
- The agent's side of the CDP integration (it's whatever CDP client the agent picked).
