# ADR-009: Daily-use UX hardening

## Status

Accepted — post-Track-B (2026).

## Context

After Track B landed the security-critical surface — bearer auth, single-use
challenge, CORS, rate limiting, MCP scaffold — the remaining friction was no
longer about *whether* Coral is correct, but about whether it's pleasant enough
to use daily. A review of the implementation surfaced concrete pain points:

- **Token rotation requires re-pairing.** Tokens expire after 24h; the only
  way to renew was to restart the daemon, get a fresh challenge, and paste it
  into the extension. No client survives a restart cleanly.
- **No revocation surface.** Track B's prompt deferred `DELETE /tokens` to
  post-v1, but without it there was no way to "log out this agent" or recover
  from a suspected compromise short of wiping the vault.
- **No visibility from the CLI.** `coral status` only checked file presence.
  Users had no way to see "is the daemon running, what agents are connected,
  what did they do today" without writing curl commands by hand.
- **Audit log was unreachable from a terminal.** The HTTP endpoint exists; the
  ergonomic CLI wrapper didn't.
- **Operational logging was tangled with audit.** Transport-level events
  (handshake attempts, middleware rejections, vault timing) were either silent
  or polluting the audit log.

## Decision

### Token lifecycle: refresh, revoke, list

- **`POST /auth/refresh`** mints a fresh token using a still-valid one. Old
  token is revoked immediately; clients hold at most one valid token at a time.
  This is the pattern that lets a long-running extension survive 24h+ without
  re-pairing.
- **`GET /tokens`** returns the list of active tokens (hash, name, timestamps).
  Used by `coral status` to show connected agents and by `coral panic` to
  enumerate revocation targets. The token *hash* is exposed because that's the
  only handle non-secret enough to put in an API; raw tokens never leave the
  daemon after handshake.
- **`DELETE /tokens/{token_hash}`** explicit revocation. The Track B prompt
  deferred this; we ship it because the UX argument (panic recovery, "log out
  this agent") outweighs the deferral. Revoking your own token is allowed —
  callers should expect their next request to 401, which is intentional.

### Trust recovery: `coral panic`

A single command revokes every token and every active session, then SIGTERMs
the daemon. Implementation order matters:

1. Revoke every session (need an authenticated token).
2. Revoke every other token next.
3. Revoke the panic-driver's own token last (after this, no further HTTP call
   could succeed — that's intentional).
4. SIGTERM the daemon process.

If the daemon isn't running (no PID file or stale), panic falls back to opening
the vault directly (passphrase prompt) and zeroing tokens / revoking sessions
in-process. Either path leaves the vault file intact: sessions are *marked
revoked*, not deleted, so the user can re-init or audit afterwards.

### Visibility: `coral status` and `coral audit`

- **`coral status`** queries `/sessions` and `/tokens` via the bridge token
  (`$CORAL_HOME/cli.token`) and prints daemon liveness + active-session count
  + connected-agent names.
- **`coral audit`** queries `/audit` with `--since`, `--limit`, and a substring
  filter on `event_type`. Plain text output suitable for `grep` and `less`.

Both commands use the existing `cli.token` bridge — they're thin wrappers
around the existing HTTP endpoints, not separate code paths.

### Operational logging: `coral.diag`

A new module emits one JSON line per event to stderr. Distinct from the audit
log:

- **Audit** = user-visible events, in the encrypted vault, queryable via
  `/audit`. Discipline: never logs anything reversible to a credential.
- **Diag** = operator-visible events, on stderr, filterable via
  `CORAL_DIAG_LEVEL=debug|info|warn|error`. Same discipline: no tokens, no
  challenges, no payloads.

Wired into auth-failure paths (HTTP API and MCP HTTP) and handshake-success.
The two surfaces are disjoint by event-type taxonomy.

## Consequences

- A logged-in extension can stay logged in indefinitely (refresh → refresh →
  refresh) without seeing the challenge again.
- Users have a one-command panic button that doesn't require knowing the
  passphrase if the daemon is up.
- Audit and ops logging are separate concerns again — grepping stderr for
  `auth.rejected` no longer pollutes the audit table.

## Accepted limitations

- The bridge token (`cli.token`) is still required to drive HTTP-API-based
  CLI commands. If the daemon is up but the file was deleted manually, those
  commands fail with "bridge token missing" and the user must restart the
  daemon. ADR-007 already documents this trust boundary.
- `coral audit`'s output is plain text; structured output (`--json`) is queued
  but not shipped. Add when the first user asks for it.
- `POST /auth/refresh` revokes the old token immediately. A grace-period
  variant (old token valid for 5s after refresh) would let clients hand over
  without a race window, but adds complexity for a problem we haven't seen
  yet. Add when something hits the race.

## When to revisit

- When a tray-app UI lands, fold `coral status` and `coral audit` into it.
- When per-agent policy filtering ships (week 3+), extend `coral status` to
  show what each agent is *allowed* to do in addition to what it's done.
- When the operational logging volume becomes meaningful, swap stderr for a
  rotating file under `$CORAL_HOME/logs/`.
