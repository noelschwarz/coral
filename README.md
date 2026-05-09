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

# Drive the daemon end-to-end with the printed challenge:
curl -X POST http://127.0.0.1:8765/auth/handshake \
  -H "Content-Type: application/json" \
  -d '{"challenge":"ABCD-EFGH-JKLM-NPQR","client_name":"curl-test"}'
# response: {"token":"...","expires_at":...}

curl http://127.0.0.1:8765/sessions \
  -H "Authorization: Bearer <token>"

uv run coral stop                  # if you run start in the background with tooling that backgrounds processes
```

The challenge is single-use: the first successful `/auth/handshake` consumes it.
Restart the daemon to mint a new challenge. Tokens default to 24h for extension
clients and 30 days for the CLI bridge token (configurable via `Config`).
Long-lived clients should call `POST /auth/refresh` before expiry instead of
re-pairing through the challenge.

### Daily-use commands

```bash
uv run coral status                # daemon state, active sessions, connected agents
uv run coral audit --since 0 --limit 50
uv run coral audit --event-type session.captured
uv run coral panic --yes           # revoke everything + stop daemon (trust recovery)
```

`coral status` and `coral audit` use the bridge token in `$CORAL_HOME/cli.token`,
written automatically when `coral start` runs.

### Operational logging

The daemon emits structured JSON events to stderr (separate from the audit log
in the vault). Filter with `CORAL_DIAG_LEVEL=debug|info|warn|error` (default
`info`).

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
