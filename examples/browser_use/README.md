# Example: Coral + [`browser-use`](https://github.com/browser-use/browser-use)

`browser-use` is a popular Python framework for LLM-orchestrated browser
automation. It typically launches its own browser; here we hand it a
Coral-managed authenticated browser instead, so the LLM operates on a
session you logged into yourself.

## Prerequisites

- `coral` on your `PATH`, with at least one captured session.
- An LLM API key — set `OPENAI_API_KEY` (or whatever your `browser-use`
  config expects).
- The dependencies in [`requirements.txt`](requirements.txt).

## Run it

```sh
cd examples/browser_use
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

export OPENAI_API_KEY=sk-…
export CORAL_SESSION_ORIGIN=https://github.com   # or whichever you captured

python main.py
```

You'll see the agent's reasoning in stderr while it drives the
restored browser.

## What's happening

1. We connect to Coral over stdio MCP and call `coral_open_session` for
   the session matching `$CORAL_SESSION_ORIGIN`.
2. Coral hands back a CDP URL for an isolated, authenticated Chromium.
3. We give `browser-use`'s `Agent` that CDP URL via the `cdp_url`
   parameter, so it attaches to the Coral-launched browser rather than
   launching its own.
4. The agent does its thing. Every navigation is still policy-checked by
   Coral — if it tries to hit a denied path, it'll see an aborted load.
5. We close the session via Coral when the agent finishes.

## Adapting it

The natural-language `task` at the bottom of `main.py` is just a string.
Swap it for whatever the agent should do. Coral keeps the policy
boundaries intact regardless of what the LLM decides to do next.
