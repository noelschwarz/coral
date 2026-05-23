# Coral examples

Runnable examples showing how to drive an authenticated Coral session
from real agent frameworks. Each example assumes you already captured a
session via the Chrome extension and that `coral list` shows it. If not,
work through the [Quickstart](../README.md#quickstart) first.

| Example | Language | What it shows |
|---|---|---|
| [`python_mcp/`](python_mcp/) | Python | Pure `mcp` Python SDK + Playwright. The smallest possible end-to-end loop. |
| [`browser_use/`](browser_use/) | Python | Drive Coral with [`browser-use`](https://github.com/browser-use/browser-use)'s LLM-orchestrated browser agent. |
| [`stagehand/`](stagehand/) | TypeScript | Drive Coral with [Stagehand](https://github.com/browserbase/stagehand). |

Every example follows the same shape:

1. Spawn `coral mcp-stdio` over stdio (or connect to the running HTTP
   daemon).
2. Call `coral_list_sessions` to find a captured session.
3. Call `coral_open_session` to get a fresh CDP URL backed by the
   restored, isolated Chromium.
4. Drive the browser through your framework of choice.
5. Call `coral_close_session` to tear it down.

The point of these isn't to be production-ready scripts — they're the
shortest path from "I installed Coral" to "an agent is doing something
useful". Copy, modify, and discard.

## Running an example

```sh
# Make sure Coral itself is installed and the daemon is running
coral status

# Then in each example directory, follow that example's README.
```

Examples never import Coral as a library; they only talk to it via MCP.
That mirrors how real agents will plug in. If you want to embed Coral
inside a Python process directly, that's `coral.vault` and friends —
out of scope for this directory.
