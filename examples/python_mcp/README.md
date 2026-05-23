# Example: pure Python + `mcp` SDK + Playwright

The shortest possible end-to-end loop:

1. Spawn `coral mcp-stdio` as a subprocess.
2. Pick the first captured session.
3. Open it, get a CDP URL, drive a page with Playwright.
4. Close.

This is the cleaned-up, runnable version of the snippet in the project
[README](../../README.md#use-it-from-an-mcp-agent).

## Prerequisites

- `coral` on your `PATH` (or change `command` in `main.py`).
- At least one captured session — verify with `coral list`.
- The dependencies in [`requirements.txt`](requirements.txt).

## Run it

```sh
cd examples/python_mcp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python main.py
```

You should see something like:

```
Connecting to Coral via stdio…
Found 1 captured session(s):
  - 8a7c…  https://github.com   (active)
Opening session for https://github.com …
Driving the restored browser:
  title = "Issues · …"
Closed.
```

## What to look for

- The Chromium that pops up is **not** your normal Chrome. It's a fresh,
  isolated profile that Coral spun up with cookies/localStorage restored
  for the captured origin. Closing it doesn't touch your daily browser.
- The agent (this script) never received your password — only a CDP URL
  pointing at an already-authenticated browser.
- Every navigation the script performs is checked against the origin's
  policy. Try changing the `page.goto(...)` target to a denied path and
  you'll see it abort.
