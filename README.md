# Coral — `sudo` for browser agents

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#status)
[![CI](https://github.com/noelschwarz/coral/actions/workflows/ci.yml/badge.svg)](https://github.com/noelschwarz/coral/actions)

> **Status: alpha — pre-audit.** Coral has not yet undergone an external
> security review. The design ([THREAT_MODEL.md](THREAT_MODEL.md)) and ADR
> series document our threat posture transparently, but until a v1.0 release
> ships with a third-party review, treat Coral as an experimental tool. Don't
> use it to broker sessions for accounts whose compromise you can't tolerate.

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

`pip install coralbridge` isn't published yet (waiting on the extension's Chrome Web Store submission; see ADR-013). For now:

```sh
git clone https://github.com/noelschwarz/coral
cd coral
uv sync --all-extras
uv run playwright install chromium
```

Coral requires Python 3.11+. On Linux you need `libsqlcipher-dev` (`apt install libsqlcipher-dev`). macOS pulls in SQLCipher via `brew install sqlcipher`. Windows is not in the supported matrix yet (per ADR-013).

## Quickstart — 3 steps

Open a terminal in the repo root:

```sh
uv run coral up
```

That single command:
- Initializes the vault (prompts for a passphrase the first time).
- Starts the daemon detached in the background.
- **Copies the handshake challenge to your clipboard.**

Then build and load the extension:

```sh
cd extension
npm ci
npm run build
```

In Chrome:
1. `chrome://extensions` → enable **Developer mode** → **Load unpacked** → pick `extension/dist/`.
2. Click the Coral icon. The popup detects the clipboard challenge and pre-fills the input.
3. Press **Pair**.

Done. Navigate to a site you're logged into → click the Coral icon → **Capture session**.

### For daily use (optional but recommended)

```sh
uv run coral install-service
```

Writes a launchd LaunchAgent (macOS) or systemd `--user` unit (Linux) so the daemon auto-starts on login. No more "keep this terminal open."

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
- ADR series — `docs/ADR-006` through `docs/ADR-017` for individual decisions.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the development workflow,
quality gates, and DCO sign-off policy. The project follows the
[Contributor Covenant 2.1](CODE_OF_CONDUCT.md).

## Community

- **Questions, design discussions, show-and-tell:** GitHub Discussions on this repo.
- **Bugs and feature requests:** GitHub Issues.
- **Security reports:** see [`SECURITY.md`](SECURITY.md) — please use private
  vulnerability reporting, not a public issue.

## License

Licensed under the [Apache License, Version 2.0](LICENSE). Contributions are
accepted under the same license.
