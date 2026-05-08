# Coral (daemon + CLI)

Local-first **browser session bridge** for AI agents: Chrome extension + Python daemon + MCP (engineering spec: `coral-engineering-spec.md`).

## Development quickstart

```bash
git clone <repo-url>
cd coral
uv sync --all-extras
uv run coral init                  # creates ~/.coral/vault.db (+ vault_meta.json); use CORAL_HOME for tests
uv run coral start                 # foreground daemon; prints handshake challenge; Ctrl+C to stop
# in another terminal:
curl http://127.0.0.1:8765/healthz
uv run coral stop                  # if you run start in the background with tooling that backgrounds processes
```

Environment variables:

- `CORAL_HOME` — data directory (default `~/.coral`).
- `CORAL_PASSPHRASE` — non-interactive passphrase for `init` / `start` (CI and scripts).
- `CORAL_HTTP_HOST`, `CORAL_HTTP_PORT`, `CORAL_MCP_HTTP_PORT` — optional overrides (see `coral/config.py`).

### Tests

```bash
uv run pytest tests/ --cov=coral --cov-report=term-missing
uv run pyright coral
```

### User-facing docs (placeholder)

End-user flows (Chrome Web Store install, extension handshake UX, capture demo) are still **TODO** for v1 polish.
