# Example: Coral + [`browser-use`](https://github.com/browser-use/browser-use) inside your code

`browser-use` is a popular Python framework for LLM-orchestrated
browser automation. By default it launches its own browser; this
example shows the canonical pattern for using Coral as the **session
manager** inside `browser-use`'s host application — so the agent
operates on a session **you** logged into yourself, with no password
or cookie ever crossing the agent boundary.

## The pattern (drop into your own code)

The whole thing is one async function. Lift it into your application
verbatim — a CLI subcommand, a FastAPI handler, a background worker, a
notebook cell, whatever:

```python
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from browser_use import Agent, Browser
from langchain_openai import ChatOpenAI


async def run_against_my_session(task: str, origin: str) -> str:
    """Drive an authenticated browser via an LLM, without ever handling the
    user's password. Coral owns the session; browser-use drives it."""
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
                browser=Browser(cdp_url=cdp_url),  # Coral's isolated Chromium
            )
            return str(await agent.run())
        finally:
            await coral.call_tool("coral_close_session", {"session_handle": handle})
```

That's the whole story.

- **Coral** is your session manager. It provides an authenticated,
  policy-checked, isolated Chromium for one task and tears it down
  cleanly afterward.
- **browser-use** is your action loop. Its LLM never sees a password,
  never gets a long-lived browser handle, never decides which session
  to use — that's your application's job.
- **They meet at `cdp_url`**. browser-use attaches to the Chromium
  Coral launched, and every navigation it makes still flows through
  Coral's policy engine. Denied paths abort before the network call.

## Prerequisites

- `coral` on your `PATH` with at least one captured session
  (`coral list`).
- An LLM API key for whichever provider you give to `browser-use`
  (`OPENAI_API_KEY` for the snippet above).
- The dependencies in [`requirements.txt`](requirements.txt).

## Smoke-testing this example as a script (optional)

If you want to confirm everything is wired up before embedding the
pattern in your application:

```sh
cd examples/browser_use
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

export OPENAI_API_KEY=sk-…
export CORAL_SESSION_ORIGIN=https://github.com   # whichever you captured

python main.py
```

`main.py` is a thin runnable wrapper around the same pattern shown
above; it exists so you can verify the install in one command. The
real artifact is the snippet — copy it into your codebase, swap the
`task` string for what your agent should actually do, ship.
