"""Drive Coral with `browser-use`'s LLM agent.

Coral provides the authenticated, isolated Chromium; `browser-use`
provides the LLM-driven action loop. The two meet at the CDP URL.
"""

from __future__ import annotations

import asyncio
import os
import sys

from browser_use import Agent, Browser  # type: ignore[import-not-found]
from langchain_openai import ChatOpenAI  # type: ignore[import-not-found]
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def _pick_session(sessions: list[dict], origin: str | None) -> dict:
    if origin:
        for s in sessions:
            if s["origin"] == origin and s["state"] == "active":
                return s
        raise RuntimeError(
            f"No active session for {origin!r}. Captured sessions: "
            f"{[s['origin'] for s in sessions]}"
        )
    for s in sessions:
        if s["state"] == "active":
            return s
    raise RuntimeError("No active captured sessions. Capture one first.")


async def main() -> int:
    if "OPENAI_API_KEY" not in os.environ:
        print("Set OPENAI_API_KEY before running.", file=sys.stderr)
        return 2

    origin = os.environ.get("CORAL_SESSION_ORIGIN")
    task = os.environ.get(
        "TASK",
        "Summarize my unread notifications in three bullet points.",
    )

    server = StdioServerParameters(command="coral", args=["mcp-stdio"])
    async with stdio_client(server) as (read, write), ClientSession(read, write) as s:
        await s.initialize()

        listing = await s.call_tool("coral_list_sessions", {})
        sessions = listing.structuredContent["sessions"]
        chosen = _pick_session(sessions, origin)

        print(f"Opening Coral session for {chosen['origin']} …", file=sys.stderr)
        opened = await s.call_tool(
            "coral_open_session",
            {
                "session_id": chosen["session_id"],
                "purpose": "examples/browser_use",
            },
        )
        cdp_url = opened.structuredContent["cdp_url"]
        handle = opened.structuredContent["session_handle"]

        try:
            browser = Browser(cdp_url=cdp_url)
            agent = Agent(
                task=task,
                llm=ChatOpenAI(model="gpt-4o"),
                browser=browser,
            )
            await agent.run()
        finally:
            await s.call_tool("coral_close_session", {"session_handle": handle})
            print("Closed Coral session.", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
