# Coral (daemon + CLI)

Local-first **browser session bridge** for AI agents: Chrome extension + Python daemon + MCP (engineering spec: `coral-engineering-spec.md`).

## Development quickstart

```bash
git clone <repo-url>
cd coral
uv sync --all-extras
uv run coral init                  # creates ~/.coral/vault.db (+ vault_meta.json); use CORAL_HOME for tests
uv run coral start                 # foreground daemon; prints handshake challenge; Ctrl+C to stop
# in another terminal:
curl http://127.0.0.1:8765/healthz

# Drive the daemon end-to-end with the printed challenge:
curl -X POST http://127.0.0.1:8765/auth/handshake \
  -H "Content-Type: application/json" \
  -d '{"challenge":"ABCD-EFGH-JKLM-NPQR","client_name":"curl-test"}'
# response: {"token":"...","expires_at":...}

curl http://127.0.0.1:8765/sessions \
  -H "Authorization: Bearer <token>"

uv run coral stop                  # if you run start in the background with tooling that backgrounds processes
```

The challenge is single-use: the first successful `/auth/handshake` consumes it.
Restart the daemon to mint a new challenge. Tokens default to 24h for extension
clients and 30 days for the CLI bridge token (configurable via `Config`).
Long-lived clients should call `POST /auth/refresh` before expiry instead of
re-pairing through the challenge.

### Daily-use commands

```bash
uv run coral status                # daemon state, active sessions, connected agents
uv run coral audit --since 0 --limit 50
uv run coral audit --event-type session.captured
uv run coral list                  # captured sessions
uv run coral revoke https://x.com  # revoke active session(s) for an origin
uv run coral panic --yes           # revoke everything + stop daemon (trust recovery)
```

### Policy & review

```bash
uv run coral policy get https://github.com           # print the YAML
uv run coral policy put https://github.com -f p.yaml # upload
uv run coral reviews list                            # pending operator reviews
uv run coral approve <review_id>
uv run coral deny    <review_id>
```

Six bundled behavior packs (GitHub, Gmail, Linear, LinkedIn, Notion, Slack)
seed on `coral init` with conservative defaults — `default_action: deny` plus
explicit `allowed_paths`. See [`docs/policy-language.md`](docs/policy-language.md)
for the YAML schema and decision semantics; [ADR-011](docs/ADR-011-policy-engine.md)
for the design rationale.

`coral status` and `coral audit` use the bridge token in `$CORAL_HOME/cli.token`,
written automatically when `coral start` runs.

### Operational logging

The daemon emits structured JSON events to stderr (separate from the audit log
in the vault). Filter with `CORAL_DIAG_LEVEL=debug|info|warn|error` (default
`info`).

### End-to-end via MCP

Once a session is captured (via the extension or a direct `POST /sessions`),
any MCP-speaking agent can drive it:

```python
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp import ClientSession
from playwright.async_api import async_playwright

server = StdioServerParameters(command="coral", args=["mcp-stdio"])
async with stdio_client(server) as (read, write), ClientSession(read, write) as s:
    await s.initialize()
    res = await s.call_tool(
        "coral_open_session",
        {"session_id": "<uuid from coral list>", "purpose": "read my feed"},
    )
    cdp_url = res.structuredContent["cdp_url"]
    handle  = res.structuredContent["session_handle"]

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(cdp_url)
        ctx = browser.contexts[0]              # restored, isolated, authenticated
        page = await ctx.new_page()
        await page.goto("https://www.linkedin.com/feed/")
        # ... drive the agent ...
        await browser.close()

    await s.call_tool("coral_close_session", {"session_handle": handle})
```

Each `coral_open_session` launches its own Chromium process for isolation
(ADR-010). The agent gets a CDP URL, connects with any CDP client (Playwright,
Puppeteer, raw CDP), and drives the browser. Every navigation is audited.

Environment variables:

- `CORAL_HOME` — data directory (default `~/.coral`).
- `CORAL_PASSPHRASE` — non-interactive passphrase for `init` / `start` (CI and scripts).
- `CORAL_HTTP_HOST`, `CORAL_HTTP_PORT`, `CORAL_MCP_HTTP_PORT` — optional overrides (see `coral/config.py`).

### Tests

```bash
uv run pytest tests/ --cov=coral --cov-report=term-missing
uv run pyright coral
```

### User-facing docs (placeholder)

End-user flows (Chrome Web Store install, extension handshake UX, capture demo) are still **TODO** for v1 polish.
