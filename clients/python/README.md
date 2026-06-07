# coral-cloud (Python SDK)

Give AI agents authenticated browser sessions with an API key — no
daemon, no extension, no native dependencies.

> **Status: design-stage / alpha skeleton.** The hosted Coral Cloud
> backend does not exist yet (see
> [ADR-019](../../docs/ADR-019-cloud-architecture.md)). This package is
> built against the published wire contract
> ([`protocol/openapi.yaml`](../../protocol/openapi.yaml)) and exercised
> against an in-process fake. It will talk to a real server once Track
> CLOUD-2 lands. Don't ship it to production yet.

This is the **cloud** counterpart to the local
[`coralbridge`](../../README.md) daemon. Same idea — an agent drives an
authenticated browser it doesn't have the password for — but the browser
and the encrypted session live in Coral's cloud, and you reach them with
an API key instead of running anything locally. Read
[THREAT_MODEL-cloud.md](../../THREAT_MODEL-cloud.md) before you decide
which product fits: Cloud trades the local product's
"credentials-never-leave-my-machine" guarantee for zero-install
convenience.

## Install

```sh
pip install coral-cloud
```

## Use

```python
from coral_cloud import CoralCloud

coral = CoralCloud()  # reads CORAL_API_KEY from the environment

# 1) One-time: capture a session by logging in through a hosted browser.
login = coral.start_login("https://github.com")
print("Open and log in:", login.live_view_url)   # hand this to the user
login = coral.wait_for_login(login.login_id)      # blocks until done

# 2) Anytime: hand an agent an authenticated browser.
with coral.session(login.session_id, purpose="triage issues") as s:
    # s.cdp_url is a wss:// endpoint. Drive it with any CDP client:
    #   from playwright.sync_api import sync_playwright
    #   browser = pw.chromium.connect_over_cdp(s.cdp_url)
    ...
```

The `open_session` response mirrors the local product's
`coral_open_session`, so existing browser-use / Stagehand integrations
are drop-in — only the source of `cdp_url` changes. See
[`examples/browser_use_cloud.py`](examples/browser_use_cloud.py).

## Configuration

| | |
|---|---|
| `CORAL_API_KEY` | Project API key (`ck_live_…`). Or pass `api_key=`. |
| `CORAL_BASE_URL` | Override the API base URL. Or pass `base_url=`. |

## Errors

All errors derive from `CoralError`. The HTTP-mapped ones are
`CoralAuthError` (401), `CoralNotFoundError` (404),
`CoralConflictError` (409), `CoralRateLimitError` (429), and the generic
`CoralAPIError`.

## Develop

```sh
cd clients/python
pip install -e ".[dev]"
pytest
```

Tests inject a `FakeTransport` (see `tests/conftest.py`), so no network
or server is required.
