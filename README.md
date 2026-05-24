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

## What it looks like in your code

The dominant use case is **embedding Coral as a session manager inside
your own agent code**. You log in to a site once in your real Chrome,
Coral persists the session in an encrypted vault, and your application
hands the agent of your choice an authenticated, isolated browser via
CDP. The agent never sees a password.

Here's the whole pattern with [`browser-use`](https://github.com/browser-use/browser-use):

```python
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from browser_use import Agent, Browser
from langchain_openai import ChatOpenAI


async def run_against_my_session(task: str, origin: str) -> str:
    """Drive an authenticated browser via an LLM, without ever handling
    the user's password. Coral owns the session; browser-use drives it."""
    server = StdioServerParameters(command="coral", args=["mcp-stdio"])

    async with stdio_client(server) as (r, w), ClientSession(r, w) as coral:
        await coral.initialize()

        listing = await coral.call_tool("coral_list_sessions", {})
        sessions = listing.structuredContent["sessions"]
        chosen = next(
            s for s in sessions if s["origin"] == origin and s["state"] == "active"
        )

        opened = await coral.call_tool(
            "coral_open_session",
            {"session_id": chosen["session_id"], "purpose": task},
        )
        cdp_url = opened.structuredContent["cdp_url"]
        handle  = opened.structuredContent["session_handle"]

        try:
            agent = Agent(
                task=task,
                llm=ChatOpenAI(model="gpt-4o"),
                browser=Browser(cdp_url=cdp_url),   # Coral's isolated Chromium
            )
            return str(await agent.run())
        finally:
            await coral.call_tool("coral_close_session", {"session_handle": handle})
```

That snippet drops into any Python codebase — a CLI subcommand, a
FastAPI handler, a worker, a notebook cell. **Coral provides the
authenticated, policy-checked browser; `browser-use` provides the
LLM-driven action loop. They meet at the CDP URL.** Every navigation
the agent makes still flows through Coral's policy engine; denied
paths abort before the network call.

The same pattern works with [Stagehand](https://github.com/browserbase/stagehand)
in TypeScript, or with the raw `mcp` SDK + Playwright if you're
building your own action loop —
see [`examples/`](examples/) for both.

## Requirements

- **Python 3.11+**
- **macOS** or **Linux** (Windows is not supported yet)
- SQLCipher native library:
  - macOS — `brew install sqlcipher`
  - Linux (Debian/Ubuntu) — `sudo apt install libsqlcipher-dev`
- [**uv**](https://docs.astral.sh/uv/) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Node 20+** — only if you build the Chrome extension from source (optional
  when using the pre-built `coral-extension-v0.6.0.zip`)

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

### 2. Load the extension

**Pre-built bundle (recommended)** — no Node.js required. Download
[`coral-extension-v0.6.0.zip`](https://github.com/noelschwarz/coral/releases/download/v0.6.0/coral-extension-v0.6.0.zip)
into `extension/`, then:

```sh
cd extension
mkdir -p dist-v0.6.0
unzip -o coral-extension-v0.6.0.zip -d dist-v0.6.0
```

Load `extension/dist-v0.6.0/` in Chrome (step 3 below). The zip is
gitignored so release bundles stay out of the repo.

**Build from source** — only if you need unreleased extension changes:

```sh
cd extension
npm ci
npm run build
```

Load `extension/dist/` instead.

In Chrome:

1. Open `chrome://extensions`.
2. Toggle **Developer mode** (top-right).
3. Click **Load unpacked** and select `extension/dist-v0.6.0/` (pre-built)
   or `extension/dist/` (from source).
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

## Wire Coral into your MCP client

If you'd rather have your IDE-resident agent (Claude Desktop, Cursor,
Claude Code) call Coral on its own — instead of embedding the snippet
above in your own code — one command writes the config:

```sh
coral mcp install --client claude-desktop   # or: cursor, claude-code
```

That writes a `coral` MCP-server entry into the client's config file
(no JSON editing). Restart the client and it'll spawn `coral mcp-stdio`
on demand. The LLM in your IDE can then call `coral_list_sessions` /
`coral_open_session` / `coral_close_session` itself as part of any chat
task. `coral mcp status --client <name>` shows the current entry;
`coral mcp uninstall --client <name>` removes it.

For embedding Coral inside your own application code, see the
[snippet above](#what-it-looks-like-in-your-code) and the
[`examples/`](examples/) directory:

- [`examples/browser_use/`](examples/browser_use/) — Python +
  [browser-use](https://github.com/browser-use/browser-use) LLM agent.
- [`examples/stagehand/`](examples/stagehand/) — TypeScript +
  [Stagehand](https://github.com/browserbase/stagehand).
- [`examples/python_mcp/`](examples/python_mcp/) — raw `mcp` SDK +
  Playwright for custom action loops.

Per-session Chromium isolation ([ADR-010](docs/ADR-010-per-session-chromium.md))
keeps every session in its own sandboxed profile no matter which
client drives it.

Copy, modify, and discard.

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
