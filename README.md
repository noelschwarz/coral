# Coral — `sudo` for browser agents

Coral is a **local-first session bridge** that lets AI agents borrow your already-authenticated browser sessions on a per-site, per-action, fully audited basis. You log in once in your real Chrome — passing 2FA, captchas, whatever — and Coral persists the resulting authenticated state in a passphrase-encrypted vault. When an agent needs to act on that site, the Coral daemon spins up a fresh isolated Chromium with your session restored and hands the agent a CDP URL it can drive. **The agent never sees your password.**

Three pieces:
- **Python daemon + CLI** (this repo) — vault, HTTP API, MCP server, Playwright session manager, policy engine.
- **Chrome extension** (`/extension/`, separate codebase) — captures sessions from your normal browsing.
- **MCP integration** — any MCP-speaking agent (Claude Desktop, Cursor, Claude Code, browser-use, Stagehand) drives Coral via stdio or HTTP.

Status: **v0.5.0 — daemon + CLI + Chrome extension shipped; PyPI publish pending.**
The mechanical thesis is provable end-to-end ([e2e test](tests/e2e/test_capture_and_restore.py)),
and the extension implements the spec §13.1 onboarding flow
([install guide](extension/INSTALL.md)).

## Install (current)

`pip install coralbridge` isn't published yet (waiting on the extension; see ADR-013). For now:

```bash
git clone https://github.com/noelschwarz/coral
cd coral
uv sync --all-extras
uv run playwright install chromium   # ~150 MB
```

Coral requires Python 3.11+. On Linux you need `libsqlcipher-dev` (`apt`/`brew`). macOS pulls in SQLCipher via `brew install sqlcipher`. Windows is not in the supported matrix yet.

## Quickstart

```bash
# 1. Create an encrypted vault. Stores the captured-session state at rest.
uv run coral init

# 2. Start the daemon. Prints a handshake challenge on stdout.
uv run coral start
# Output includes:
#     Coral daemon started.
#     HTTP API: http://127.0.0.1:8765
#     Extension handshake challenge (paste into the Coral extension popup):
#
#         ABCD-EFGH-JKLM-NPQR

# 3. In another terminal, complete the handshake (the extension will do this; via curl for now):
curl -s -X POST http://127.0.0.1:8765/auth/handshake \
  -H "Content-Type: application/json" \
  -d '{"challenge":"ABCD-EFGH-JKLM-NPQR","client_name":"curl"}'
# {"token":"...","expires_at":...}

# 4. Capture a session by POSTing the cookies + storage you want to lend the agent.
TOKEN=...
curl -s -X POST http://127.0.0.1:8765/sessions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"origin":"https://github.com","state":{"version":1,"cookies":[…]}}'

# 5. An agent connects via MCP (stdio or HTTP) and drives the session.
#    See "End-to-end via MCP" below.
```

## Daily-use commands

```bash
coral status                   # daemon state, active sessions, connected agents
coral list                     # captured sessions
coral revoke https://x.com     # revoke active session(s) for an origin
coral audit --since 0 --limit 50
coral audit --event-type session.captured
coral panic --yes              # revoke everything + stop daemon (trust recovery)
coral diagnose                 # install + security self-check
```

## Policy & review

```bash
coral policy get https://github.com               # print the active YAML
coral policy put https://github.com -f my.yaml    # upload
coral reviews list                                # pending operator reviews
coral approve <review_id>
coral deny    <review_id>
```

Six bundled behavior packs (GitHub, Gmail, Linear, LinkedIn, Notion, Slack) seed on `coral init` with `default_action: deny` plus explicit `allowed_paths`. The schema lives in [`docs/policy-language.md`](docs/policy-language.md); the rationale is in [ADR-011](docs/ADR-011-policy-engine.md).

## End-to-end via MCP

```python
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
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
        ctx = browser.contexts[0]            # restored, isolated, authenticated
        page = await ctx.new_page()
        await page.goto("https://github.com/issues")
        # ... drive the agent ...
        await browser.close()

    await s.call_tool("coral_close_session", {"session_handle": handle})
```

Each `coral_open_session` launches its own Chromium for isolation ([ADR-010](docs/ADR-010-per-session-chromium.md)). Every navigation routes through the policy engine; denied paths abort with `ERR_BLOCKED_BY_CLIENT` before the network call.

## Environment variables

- `CORAL_HOME` — data directory (default `~/.coral`).
- `CORAL_PASSPHRASE` — non-interactive passphrase for `init` / `start` (CI and scripts).
- `CORAL_HTTP_HOST`, `CORAL_HTTP_PORT`, `CORAL_MCP_HTTP_PORT` — port overrides (host stays `127.0.0.1` by hard binding; see [ADR-008](docs/ADR-008-http-api-and-mcp.md)).
- `CORAL_DIAG_LEVEL` — structured stderr log filter (`debug|info|warn|error`, default `info`).

## Architecture & docs

- [`coral-engineering-spec.md`](coral-engineering-spec.md) — the source of truth.
- [`docs/architecture.md`](docs/architecture.md) — current module map.
- [`docs/policy-language.md`](docs/policy-language.md) — YAML schema and decision semantics.
- [`THREAT_MODEL.md`](THREAT_MODEL.md) — what Coral defends against (and what it doesn't).
- [`docs/performance.md`](docs/performance.md) — baseline numbers.
- [`SECURITY.md`](SECURITY.md) — vulnerability reporting policy.
- [`docs/security-review-prep.md`](docs/security-review-prep.md) — briefing checklist for an external reviewer.
- [`CHANGELOG.md`](CHANGELOG.md) — release history.
- ADR series — `docs/ADR-006` through `docs/ADR-015` for individual decisions.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

MIT.
