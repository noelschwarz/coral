# Coral — `sudo` for browser agents

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)](THREAT_MODEL.md)
[![CI](https://github.com/noelschwarz/coral/actions/workflows/ci.yml/badge.svg)](https://github.com/noelschwarz/coral/actions)

> **Alpha — pre-audit.** Coral has not yet undergone an external security
> review. The design ([THREAT_MODEL.md](THREAT_MODEL.md)) is transparent about
> what's defended and what isn't, but until a v1.0 release ships with a
> third-party review, treat Coral as experimental. Don't use it to broker
> sessions for accounts you can't afford to have compromised.

Coral is a **local-first session bridge** that lets AI agents borrow your
already-authenticated browser sessions on a per-site, per-action, fully
audited basis. You log in once in your real Chrome — passing 2FA, captchas,
whatever — and Coral persists the resulting authenticated state in a
passphrase-encrypted vault. When an agent needs to act on that site, Coral
spins up a fresh isolated Chromium with your session restored and hands the
agent a CDP URL it can drive. **The agent never sees your password.**

## How it fits together

- **Python daemon + CLI** (this repo) — vault, HTTP API, MCP server,
  Playwright session manager, policy engine.
- **Chrome extension** (`extension/`) — captures sessions from your normal
  browsing. Submitted to the Chrome Web Store; while the listing is under
  review, load it unpacked as shown below.
- **MCP** — any MCP-speaking agent (Claude Desktop, Cursor, Claude Code,
  browser-use, Stagehand, …) drives Coral over stdio or local HTTP.

## Requirements

- **Python 3.11+**
- **macOS** or **Linux** (Windows is not supported yet)
- SQLCipher native library:
  - macOS — `brew install sqlcipher`
  - Linux (Debian/Ubuntu) — `sudo apt install libsqlcipher-dev`
- [**uv**](https://docs.astral.sh/uv/) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Node 20+** — only to build the Chrome extension

## Install

```sh
git clone https://github.com/noelschwarz/coral
cd coral
uv sync --all-extras
uv run playwright install chromium
```

If you'd rather have `coral` on your `PATH` instead of typing `uv run coral`
every time:

```sh
uv tool install .
```

## Quickstart

Three terminal commands and three Chrome clicks. End-to-end under a minute.

### 1. Start the daemon

```sh
coral up
```

`coral up` initializes the encrypted vault on first run (asks for a
passphrase), starts the daemon in the background, and **copies a pairing
challenge to your clipboard**.

### 2. Build and load the extension

```sh
cd extension
npm ci
npm run build
```

In Chrome:

1. Open `chrome://extensions`.
2. Toggle **Developer mode** (top-right).
3. Click **Load unpacked** and select the `extension/dist/` directory.
4. Pin the Coral icon to your toolbar.
5. Click the icon — the popup auto-detects the clipboard challenge — and
   press **Pair**.

### 3. Capture a session

1. Navigate to any site you're already logged into.
2. Click the Coral icon → **Capture session**.

Verify from the terminal:

```sh
coral status      # daemon state, active sessions, connected agents
coral list        # captured sessions
```

That's it — an MCP agent can now open this session via `coral_open_session`.

### Make it permanent

So you don't have to keep a terminal open or re-run `coral up` after every
reboot:

```sh
coral install-service
```

That writes a launchd LaunchAgent (macOS) or systemd `--user` unit (Linux)
and stores the vault passphrase in your OS keychain
([ADR-017](docs/ADR-017-keychain-integration.md)). The daemon now starts
automatically on login.

To undo:

```sh
coral uninstall-service
```

## Use it from an MCP agent

Any MCP client can ask Coral for an authenticated Chromium over CDP:

```python
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from playwright.async_api import async_playwright

server = StdioServerParameters(command="coral", args=["mcp-stdio"])

async with stdio_client(server) as (read, write), ClientSession(read, write) as s:
    await s.initialize()

    res = await s.call_tool(
        "coral_open_session",
        {"session_id": "<uuid from `coral list`>", "purpose": "read my feed"},
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

Each `coral_open_session` launches its own Chromium for isolation
([ADR-010](docs/ADR-010-per-session-chromium.md)). Every navigation routes
through the policy engine; denied paths abort before the network call.

## Daily commands

```sh
coral status                          # daemon state, sessions, agents
coral list                            # captured sessions
coral revoke https://example.com      # revoke session(s) for an origin
coral audit --since 0 --limit 50      # tail the audit log
coral panic --yes                     # revoke everything + stop daemon
coral diagnose                        # install + security self-check
coral keychain status                 # is the vault passphrase stashed?
```

## Policy & review

Each origin has a YAML policy declaring `allowed_paths`, `denied_paths`,
rate limits, and review thresholds. Six bundled behavior packs (GitHub,
Gmail, Linear, LinkedIn, Notion, Slack) seed at first run with
`default_action: deny` plus explicit allow-lists.

```sh
coral policy get https://github.com               # print the active YAML
coral policy put https://github.com -f my.yaml    # upload a new one
coral reviews list                                # pending operator reviews
coral approve <review_id>
coral deny    <review_id>
```

Schema in [`docs/policy-language.md`](docs/policy-language.md); rationale
in [ADR-011](docs/ADR-011-policy-engine.md).

## Environment variables

- `CORAL_HOME` — data directory (default `~/.coral`).
- `CORAL_PASSPHRASE` — non-interactive passphrase for `init` / `start`
  (CI and scripts only — prefer the OS keychain for daily use).
- `CORAL_HTTP_PORT`, `CORAL_MCP_HTTP_PORT` — port overrides. The host is
  always `127.0.0.1` ([ADR-008](docs/ADR-008-http-api-and-mcp.md)).
- `CORAL_DIAG_LEVEL` — structured stderr log filter
  (`debug|info|warn|error`, default `info`).

## Docs

- [`coral-engineering-spec.md`](coral-engineering-spec.md) — source of truth.
- [`docs/architecture.md`](docs/architecture.md) — current module map.
- [`THREAT_MODEL.md`](THREAT_MODEL.md) — what Coral defends against (and what it doesn't).
- [`docs/policy-language.md`](docs/policy-language.md) — YAML policy schema.
- [`SECURITY.md`](SECURITY.md) — vulnerability reporting policy.
- [`CHANGELOG.md`](CHANGELOG.md) — release history.
- `docs/ADR-006` … `docs/ADR-017` — individual architectural decisions.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the dev workflow, quality
gates, and DCO sign-off requirements. The project follows the
[Contributor Covenant 2.1](CODE_OF_CONDUCT.md).

## Community

- **Questions, design discussions, show-and-tell:** [GitHub Discussions](https://github.com/noelschwarz/coral/discussions).
- **Bugs and feature requests:** [GitHub Issues](https://github.com/noelschwarz/coral/issues).
- **Security reports:** use [private vulnerability reporting](https://github.com/noelschwarz/coral/security/advisories/new),
  not a public issue. Details in [`SECURITY.md`](SECURITY.md).

## License

Licensed under the [Apache License, Version 2.0](LICENSE). Contributions
are accepted under the same terms.
