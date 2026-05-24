# Coral examples

Templates for embedding Coral in your own Python or TypeScript code.

The dominant use case for Coral isn't "run this example script." It's
"my application has an agent in it, and that agent needs an
authenticated browser without my application ever seeing the user's
password." These examples are the shortest path to that pattern —
copy the snippets into your own codebase, change the task, ship.

| Example | Language | What you'll embed |
|---|---|---|
| [`browser_use/`](browser_use/) | Python | Hand a Coral-managed browser to an [`browser-use`](https://github.com/browser-use/browser-use) LLM agent. |
| [`stagehand/`](stagehand/) | TypeScript | Same, with [Stagehand](https://github.com/browserbase/stagehand). |
| [`python_mcp/`](python_mcp/) | Python | Raw `mcp` Python SDK + Playwright. Lowest level; useful if you're building your own agent loop. |

Every example follows the same five-step shape, no matter the
framework:

1. Spawn `coral mcp-stdio` from your code via the `mcp` SDK (Python or
   TypeScript). It's a subprocess of your application.
2. Call `coral_list_sessions` to find a captured session.
3. Call `coral_open_session` to get a fresh CDP URL backed by an
   isolated, restored, authenticated Chromium.
4. Drive the browser through your framework of choice.
5. Call `coral_close_session` to tear down.

Coral is **your application's session manager**. Your agent framework
of choice — `browser-use`, Stagehand, the Anthropic SDK with computer
use, whatever — is the action loop. They meet at the CDP URL Coral
returns from `coral_open_session`.

## Running them as scripts (optional, for verification)

Each example also runs as a standalone script if you want to confirm
your install is wired up before you embed the pattern in your real
codebase:

```sh
coral status                            # daemon up + at least one captured session
cd examples/<one>
# follow that example's README — `python main.py` for Python, `npm start` for Stagehand
```

But that's a smoke test, not the point. The point is the snippet you
lift out and put into your own application code.

## What's *not* in here

These examples never import `coral` as a Python library. They only
talk to it via MCP (the stdio transport — the same one Claude Desktop
and Cursor use). That mirrors how third-party agents will plug in, and
keeps the integration boundary clean.

If you genuinely need to embed Coral's vault inside the same process
as your application (rare; mostly useful for testing), `coral.vault`
and friends are importable — but that's out of scope for this
directory.
