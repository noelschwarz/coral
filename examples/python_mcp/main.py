"""Minimal Coral driver: list sessions, open one, drive a page, close.

The point: show that the loop is genuinely small. Everything interesting
in this script is the four `call_tool` lines.
"""

from __future__ import annotations

import asyncio
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from playwright.async_api import async_playwright


async def main() -> int:
    server = StdioServerParameters(command="coral", args=["mcp-stdio"])
    print("Connecting to Coral via stdio…", file=sys.stderr)

    async with stdio_client(server) as (read, write), ClientSession(read, write) as s:
        await s.initialize()

        listing = await s.call_tool("coral_list_sessions", {})
        sessions = listing.structuredContent["sessions"]
        if not sessions:
            print(
                "No captured sessions. Capture one via the Chrome extension first.",
                file=sys.stderr,
            )
            return 1

        print(f"Found {len(sessions)} captured session(s):", file=sys.stderr)
        for sess in sessions:
            print(
                f"  - {sess['session_id'][:8]}…  {sess['origin']}  ({sess['state']})",
                file=sys.stderr,
            )

        target = sessions[0]
        print(f"Opening session for {target['origin']} …", file=sys.stderr)

        opened = await s.call_tool(
            "coral_open_session",
            {
                "session_id": target["session_id"],
                "purpose": "examples/python_mcp smoke test",
            },
        )
        cdp_url = opened.structuredContent["cdp_url"]
        handle = opened.structuredContent["session_handle"]

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.connect_over_cdp(cdp_url)
                ctx = browser.contexts[0]
                page = await ctx.new_page()
                await page.goto(target["origin"])
                print("Driving the restored browser:", file=sys.stderr)
                print(f"  title = {await page.title()!r}", file=sys.stderr)
                await browser.close()
        finally:
            await s.call_tool("coral_close_session", {"session_handle": handle})
            print("Closed.", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
