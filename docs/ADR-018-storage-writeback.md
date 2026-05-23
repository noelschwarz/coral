# ADR-018: Storage write-back on session close (Track N)

## Status

Accepted — Track N (2026-05).

## Context

Track L's [ADR-017](ADR-017-keychain-integration.md) shipped the keychain
integration; immediately after going public, a contributor (Kushal,
chrome-relay) sent the question the whole project had been side-stepping:

> When a site rotates storage state mid-session (auth cookie refresh,
> CSRF rotation, server-side session bump), does Coral re-capture from
> the user's Chrome on demand, or does the isolated Chromium fail
> closed?

Today the answer is: it fails closed. The isolated Chromium runs to
completion; its cookie jar updates in-memory during the session; on
close we drop the context, the user_data_dir, and the runtime cookie
state. The next `coral_open_session` restores the originally-captured
state. Any `Set-Cookie` updates the server made during the session are
lost. For short-lived sessions this doesn't matter. For multi-day
captures with regular auth-token refresh, the session goes stale faster
than it should and the user has to re-capture from their main Chrome
via the extension.

This is the boundary that determines how aggressive the policy engine
can be: if the daemon refuses to persist anything an agent's session
touched, the daemon is forced to fail closed on every staleness signal.
If the daemon writes everything back unconditionally, the agent gains a
write path into the vault that survives the session — which is exactly
the kind of capability the threat model (T6) was designed to deny.

## Decision

### 1. Policy-gated write-back, not unconditional

At session-close time, **diff** the running Chromium's cookie jar
against the captured state blob, then **persist deltas filtered by the
policy** that was active when the session opened. Specifically:

- For each cookie that exists in the live jar:
  - Compute its `(name, domain, path)` key.
  - Check whether the cookie's `path` is admissible under the
    policy's `allowed_paths` (algorithm below).
  - If admissible: persist the live value (overwriting the captured
    value for that key, or adding it as new).
  - If inadmissible: preserve the captured value if there was one;
    otherwise drop the cookie entirely.
- Cookies that exist in the captured state but no longer in the live
  jar are preserved. We deliberately don't propagate deletions because
  expiry vs. agent-clear-cookies vs. server-clear-cookies are
  indistinguishable from the daemon's vantage point; conservative
  default is "keep the original".

The audit log gets a new event type `session.state_written_back` with
counts: `updated`, `added`, `dropped_by_policy`.

### 2. Cookie-path admissibility

A cookie at `path=P` is sent for URLs with paths starting with `P`. We
admit a cookie delta if:

- `P == "/"` and the policy has at least one `allowed_paths` entry
  (i.e. the origin is at least partially trusted; cookies at `/` apply
  to anything the agent was allowed to touch); **or**
- the **literal prefix** of any `allowed_paths` entry — everything
  before the first glob character (`*`, `?`, `[`) — starts with `P`
  (i.e. the cookie applies to URLs the policy allowed).

This admits the common case (cookies at `/`, policy allows
`/issues/**`) and rejects the suspicious case (cookies at `/admin`,
policy allows only `/api/**`). It's deliberately permissive at `/`
because that's where 95%+ of real cookies land; tightening this is
straightforward later.

### 3. What we deliberately do **not** persist

- **localStorage / sessionStorage deltas.** Cookies are the staleness
  driver in practice (refresh tokens, CSRF tokens, server-side session
  IDs); local/session storage write-back is more invasive and lower
  ROI. Re-evaluate in a follow-up.
- **Anything from sessions closed with reason `session_revoked` or
  `daemon_shutdown`.** Revoke is the user explicitly distrusting the
  session; persisting on shutdown risks corrupting state if the daemon
  is mid-crash.
- **Anything from a session that opened against a policy with no
  `allowed_paths`.** A policy with no allowed paths is operating on
  pure default-deny semantics; admitting deltas here would invent
  trust the operator didn't grant.
- **IndexedDB / service workers.** Already deferred in spec §6.4.

### 4. Failure isolation

Write-back is wrapped so any error (Playwright context already gone,
vault write failure, compression failure) emits a warning and lets
`close()` proceed normally. Write-back is a "would be nice" — we never
let it block the close path or surface user-visible errors.

## Threat model impact

**New write path into the vault, originating from agent-driven
browsing.** This is a meaningful change.

- **T6 (malicious agent with valid CDP access).** Was: agent can act
  within session scope until close, but cannot mutate persistent vault
  state. Now: agent can mutate cookies that pass policy-path
  admissibility. Impact: an agent could try to "lock the user out" by
  rotating its own session cookie to a value the server doesn't accept;
  next session-open would inherit the broken state. Mitigation: this
  only works for cookies whose path is policy-admitted, which the
  operator already trusts; and the user can always re-capture from
  Chrome to recover. Net: capability expanded slightly within already-
  trusted scope.
- **T5 (policy engine integrity).** No change. The policy used to
  evaluate write-back is the one that opened the session — the same
  policy that gated every navigation during it. Tampering with policy
  mid-session doesn't change the write-back filter.

`THREAT_MODEL.md` should be updated alongside this PR to call this out.

## Consequences

- **Reduced staleness for multi-day sessions.** Refresh-token rotation,
  server session-id bumps, and similar in-flight cookie updates now
  survive `close()`.
- **The boundary Kushal asked about is now nameable.** "Still the
  captured session" extends through any cookie update that the policy's
  allowed-paths-prefix admits. Beyond that, fail-closed + re-capture
  (the existing `kill_on_redirect_to_login` path) remains the answer.
- **An ADR exists to point at.** Future contributors arguing about
  whether to persist X have a written rationale to push against.

## Accepted limitations

- localStorage / sessionStorage are not yet persisted.
- The admissibility heuristic uses literal prefixes from glob patterns;
  patterns with leading globs (`**/admin`) are not admitted. We accept
  this — leading-glob `allowed_paths` are unusual in real policies and
  treating them safely is non-trivial.
- We don't propagate deletions. Long-running sessions where the agent
  clears its own cookies will see them re-appear on next open. Tradeoff
  vs. the risk of permanently clobbering state on a transient hiccup;
  conservative default wins.

## When to revisit

- **The next time a user reports staleness despite write-back.** That
  means we need either localStorage write-back or a recapture-on-401
  prompt (PR N3, planned).
- **External security review.** Reviewers should specifically evaluate
  whether the path-admissibility heuristic is conservative enough for
  the threat model.
- **If we see real-world `allowed_paths` patterns with leading globs.**
  We'd need a proper glob-overlap check rather than literal-prefix
  matching.
