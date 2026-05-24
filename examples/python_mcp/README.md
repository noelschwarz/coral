# Example: pure Python + `mcp` SDK + Playwright inside your code

The lowest-level pattern. Use this when you're building your own
agent loop and want raw control over each step — no LLM framework in
the middle. Most users will reach for [`browser_use/`](../browser_use)
or [`stagehand/`](../stagehand) first.

## The pattern (drop into your own code)

```python
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from playwright.async_api import async_playwright


async def with_authenticated_browser(origin: str) -> str:
    """Open a Coral-managed authenticated browser, drive it directly with
    Playwright, close cleanly. The whole pattern in 25 lines."""
    server = StdioServerParameters(command="coral", args=["mcp-stdio"])

    async with stdio_client(server) as (r, w), ClientSession(r, w) as coral:
        await coral.initialize()

        listing = await coral.call_tool("coral_list_sessions", {})
        sessions = listing.structuredContent["sessions"]
        chosen = next(s for s in sessions if s["origin"] == origin)

        opened = await coral.call_tool(
            "coral_open_session",
            {"session_id": chosen["session_id"], "purpose": "my custom loop"},
        )
        cdp_url = opened.structuredContent["cdp_url"]
        handle  = opened.structuredContent["session_handle"]

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.connect_over_cdp(cdp_url)
                ctx = browser.contexts[0]            # restored + authenticated
                page = await ctx.new_page()
                await page.goto(origin)
                title = await page.title()
                await browser.close()
                return title
        finally:
            await coral.call_tool("coral_close_session", {"session_handle": handle})
```

The function returns immediately after the browser closes, all
resources cleaned up. Wrap your own action loop around the
`await page.goto(...)` block — that's the seam.

## What to notice

- The Chromium that pops up is **not** your normal Chrome. It's a
  fresh, isolated profile that Coral spun up with cookies and
  localStorage restored for the captured origin. Closing it doesn't
  touch your daily browser.
- Your code never received the user's password — only a CDP URL
  pointing at an already-authenticated browser.
- Every navigation flows through Coral's policy engine. Try changing
  the `page.goto(...)` target to a denied path and you'll see it
  abort.

## Prerequisites

- `coral` on your `PATH` (or change `command` to a full path in the
  snippet above).
- At least one captured session — verify with `coral list`.
- The dependencies in [`requirements.txt`](requirements.txt).

## Smoke-testing this example as a script (optional)

```sh
cd examples/python_mcp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python main.py
```

`main.py` is a thin runnable wrapper around the same pattern — it
prints session listing + the title of the captured origin's home page,
then closes. Useful for one-shot verification that your daemon,
captured session, and `mcp` SDK are all wired up correctly.
