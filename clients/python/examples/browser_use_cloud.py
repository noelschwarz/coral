"""Coral Cloud + browser-use — the drop-in pattern.

Compare with ../../../examples/browser_use/main.py (the *local* MCP
version): the only thing that changes is where the CDP URL comes from.
Local hands you ``ws://127.0.0.1:…`` via MCP; Cloud hands you
``wss://…`` via an API key. The agent loop is identical.

Prerequisites (once Coral Cloud is live):

    pip install coral-cloud browser-use
    export CORAL_API_KEY=ck_live_...

NOTE: design-stage. No server implements the protocol yet, so this script
is illustrative — it shows the intended shape, not a runnable demo.
"""

from __future__ import annotations

import asyncio

from coral_cloud import CoralCloud


async def run_against_my_session(task: str, origin: str) -> str:
    """Drive an authenticated cloud browser via an LLM, no password handling.

    Coral owns the session and the browser; browser-use provides the
    LLM-driven action loop. They meet at the (remote) CDP URL.
    """
    from browser_use import Agent, Browser  # type: ignore[import-not-found]
    from langchain_openai import ChatOpenAI  # type: ignore[import-not-found]

    coral = CoralCloud()  # reads CORAL_API_KEY

    # Pick an already-captured, active session for this origin.
    sessions = coral.list_sessions(origin=origin, status="active")
    if not sessions:
        raise SystemExit(
            f"No active session for {origin}. Capture one first with "
            "coral.start_login(origin) and finish the login in the browser."
        )

    with coral.session(sessions[0].id, purpose=task) as opened:
        # opened.cdp_url is a wss:// endpoint — Coral's isolated cloud Chromium.
        agent = Agent(
            task=task,
            llm=ChatOpenAI(model="gpt-4o"),
            browser=Browser(cdp_url=opened.cdp_url),
        )
        return str(await agent.run())


def capture_session(origin: str) -> str:
    """One-time: capture a session via remote interactive login."""
    coral = CoralCloud()
    login = coral.start_login(origin)
    print(f"Open this URL and log in to {origin}:\n  {login.live_view_url}")
    login = coral.wait_for_login(login.login_id)
    if login.status != "captured" or not login.session_id:
        raise SystemExit(f"login did not complete: {login.status}")
    print(f"Captured session: {login.session_id}")
    return login.session_id


if __name__ == "__main__":
    import sys

    origin = sys.argv[1] if len(sys.argv) > 1 else "https://github.com"
    asyncio.run(run_against_my_session("Summarize my open PRs", origin))
