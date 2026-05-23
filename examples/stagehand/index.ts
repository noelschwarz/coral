/**
 * Drive Coral with Stagehand.
 *
 * Coral provides the authenticated, isolated Chromium; Stagehand
 * provides the LLM-driven act/extract/observe primitives. They meet at
 * the CDP URL returned by `coral_open_session`.
 */

import { Stagehand } from "@browserbasehq/stagehand";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

interface CoralSession {
  session_id: string;
  origin: string;
  state: string;
}

async function callTool<T>(client: Client, name: string, args: Record<string, unknown>): Promise<T> {
  const res = await client.callTool({ name, arguments: args });
  return res.structuredContent as T;
}

async function main(): Promise<void> {
  const originFilter = process.env.CORAL_SESSION_ORIGIN ?? null;
  const task = process.env.TASK ?? "Summarize my unread notifications in three bullet points.";

  const transport = new StdioClientTransport({
    command: "coral",
    args: ["mcp-stdio"],
  });
  const mcp = new Client({ name: "stagehand-example", version: "0.0.1" }, { capabilities: {} });
  await mcp.connect(transport);

  const { sessions } = await callTool<{ sessions: CoralSession[] }>(
    mcp,
    "coral_list_sessions",
    {},
  );

  const chosen = sessions.find(
    (s) => s.state === "active" && (!originFilter || s.origin === originFilter),
  );
  if (!chosen) {
    console.error(
      `No active session ${originFilter ? `for ${originFilter}` : ""}. Capture one first.`,
    );
    process.exit(1);
  }

  console.error(`Opening Coral session for ${chosen.origin} …`);
  const opened = await callTool<{ cdp_url: string; session_handle: string }>(
    mcp,
    "coral_open_session",
    {
      session_id: chosen.session_id,
      purpose: "examples/stagehand",
    },
  );

  const stagehand = new Stagehand({
    env: "LOCAL",
    localBrowserLaunchOptions: { cdpUrl: opened.cdp_url },
    modelName: "gpt-4o",
  });

  try {
    await stagehand.init();
    await stagehand.page.goto(chosen.origin);
    const result = await stagehand.page.extract({
      instruction: task,
      schema: { summary: "string" },
    });
    console.log(JSON.stringify(result, null, 2));
  } finally {
    await stagehand.close();
    await callTool(mcp, "coral_close_session", {
      session_handle: opened.session_handle,
    });
    await mcp.close();
    console.error("Closed Coral session.");
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
