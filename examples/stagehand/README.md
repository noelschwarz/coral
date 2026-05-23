# Example: Coral + [Stagehand](https://github.com/browserbase/stagehand)

Stagehand is a TypeScript/Playwright-based LLM browser-automation
framework. By default it launches a fresh browser (locally or via
Browserbase); here we point it at a Coral-managed authenticated browser
over CDP instead.

## Prerequisites

- Node 20+.
- `coral` on your `PATH` with at least one captured session
  (`coral list`).
- An LLM API key — `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` depending on
  which provider you configure in `index.ts`.

## Run it

```sh
cd examples/stagehand
npm install

export OPENAI_API_KEY=sk-…
export CORAL_SESSION_ORIGIN=https://github.com   # or whichever you captured

npm start
```

## What's happening

1. We spawn `coral mcp-stdio` and connect via the MCP TypeScript SDK.
2. `coral_list_sessions` returns the captures; we pick the one matching
   `$CORAL_SESSION_ORIGIN`.
3. `coral_open_session` returns a CDP URL for an isolated, authenticated
   Chromium.
4. We construct Stagehand with `localBrowserLaunchOptions: { cdpUrl }`
   so it attaches to Coral's browser rather than launching its own.
5. The agent runs. Coral's policy engine still gates every navigation —
   the agent sees `ERR_BLOCKED_BY_CLIENT` on denied paths.
6. We tear down via `coral_close_session`.

## Adapting it

Change the `act()` and `extract()` calls at the bottom of `index.ts` to
your real task. The pattern (connect-list-open-act-close) stays the same.
