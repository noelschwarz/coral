# Example: Coral + [Stagehand](https://github.com/browserbase/stagehand) inside your code

[Stagehand](https://github.com/browserbase/stagehand) is a
TypeScript/Playwright-based LLM browser-automation framework. This
example shows the canonical pattern for using Coral as the **session
manager** inside a Stagehand-driven application — your code owns the
session choice, Stagehand owns the action loop, and the LLM never
touches passwords or long-lived browser handles.

## The pattern (drop into your own code)

```ts
import { Stagehand } from "@browserbasehq/stagehand";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

interface CoralSession {
  session_id: string;
  origin: string;
  state: string;
}

async function runAgainstMySession(task: string, origin: string): Promise<unknown> {
  const transport = new StdioClientTransport({ command: "coral", args: ["mcp-stdio"] });
  const coral = new Client({ name: "my-app", version: "0.0.1" }, { capabilities: {} });
  await coral.connect(transport);

  try {
    const listing = await coral.callTool({ name: "coral_list_sessions", arguments: {} });
    const sessions = (listing.structuredContent as { sessions: CoralSession[] }).sessions;
    const chosen = sessions.find(s => s.origin === origin && s.state === "active");
    if (!chosen) throw new Error(`No active captured session for ${origin}`);

    const opened = await coral.callTool({
      name: "coral_open_session",
      arguments: { session_id: chosen.session_id, purpose: task },
    });
    const { cdp_url, session_handle } =
      opened.structuredContent as { cdp_url: string; session_handle: string };

    const stagehand = new Stagehand({
      env: "LOCAL",
      localBrowserLaunchOptions: { cdpUrl: cdp_url },  // Coral's isolated Chromium
      modelName: "gpt-4o",
    });

    try {
      await stagehand.init();
      await stagehand.page.goto(origin);
      return await stagehand.page.extract({
        instruction: task,
        schema: { summary: "string" },
      });
    } finally {
      await stagehand.close();
      await coral.callTool({
        name: "coral_close_session",
        arguments: { session_handle },
      });
    }
  } finally {
    await coral.close();
  }
}
```

That's the whole story.

- **Coral** is your session manager — authenticated, policy-checked,
  isolated Chromium handed back as a CDP URL.
- **Stagehand** is your LLM-driven action loop. It attaches to the
  Coral-launched browser via `localBrowserLaunchOptions.cdpUrl`
  instead of launching its own.
- Every navigation Stagehand makes still flows through Coral's policy
  engine. Denied paths surface as `ERR_BLOCKED_BY_CLIENT` to the
  agent.

## Prerequisites

- Node 20+.
- `coral` on your `PATH` with at least one captured session
  (`coral list`).
- An LLM API key — `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` depending
  on the model you configure.

## Smoke-testing this example as a script (optional)

If you want to confirm Coral + Stagehand + your API key are all wired
up before embedding the pattern in your real application:

```sh
cd examples/stagehand
npm install

export OPENAI_API_KEY=sk-…
export CORAL_SESSION_ORIGIN=https://github.com   # whichever you captured

npm start
```

`index.ts` is a thin runnable wrapper around the same pattern shown
above. The real artifact is the snippet — lift it into your TypeScript
codebase, swap the task / extraction schema, ship.
