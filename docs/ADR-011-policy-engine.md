# ADR-011: Policy engine and review flow (Track E / Week 3)

## Status

Accepted — Track E (2026).

## Context

The Track D route handler audited each navigation but enforced nothing.
Track E adds the real decision layer: per-origin YAML policies, allow/deny/
review semantics, token-bucket rate limiting, and the `pending_reviews`
table that lets operators approve high-stakes actions out-of-band.

Open decisions surfaced during implementation:

1. **Default action.** Spec §4.3 says "default-allow when no rule matches in
   v1." The Track E review opinion was to flip this to default-deny.
2. **`coral_request_review` UX.** Spec §5.2 describes a blocking flow:
   `plyer` notification → CLI approval → unblock. MCP-over-HTTP can't hold
   sync waits cleanly, and `plyer` adds a platform-specific dep.
3. **Behavior pack contents.** Spec §13.4 names six sites; LinkedIn is the
   riskiest (ToS).
4. **Rate-limiter shape.** Token bucket vs sliding window vs leaky bucket.

## Decisions

### 1. Honor spec default-allow; expose `default_action: deny` as opt-in

The `Policy` model validates `default_action: "allow" | "deny"` with `"allow"`
as default — matching the spec. Power users who want default-deny set it
explicitly. Flipping the default is a portfolio-level call; this ADR doesn't
make it.

The six bundled behavior packs all ship with `default_action: deny` plus an
explicit `allowed_paths` list — i.e. the safe posture is what users get out of
the box, even though the underlying model honors the spec's default.

### 2. `coral_request_review` is non-blocking

The MCP tool inserts a `pending_reviews` row, fires a `policy.review.requested`
audit + `diag.warn`, and returns `{review_id, status: "pending"}` immediately.
The agent decides what to do (poll, retry, abandon). The operator decides via
`coral approve <id>` / `coral deny <id>`, which write to the same row.

This diverges from spec §5.2's blocking model. Reasoning:

- MCP-over-HTTP holds drop on long idle waits; spec was written for stdio.
- A non-blocking surface composes cleanly with both transports.
- The CLI is the v1 review UI. A Chrome-extension notification or push
  channel can hook into the same `pending_reviews` rows in v1.x without an
  API change.
- `plyer` is deferred entirely. When a notification surface lands (extension
  push, ntfy webhook, Pushover), it taps into the same `policy.review.requested`
  audit event.

### 3. Behavior packs ship, LinkedIn caveat called out

Six packs (`github`, `gmail`, `linear`, `linkedin`, `notion`, `slack`) seeded
into the `policies` table on `coral init` if not already present. Each is
`default_action: deny` with explicit allowed paths. The LinkedIn pack carries
a top-of-file warning about LinkedIn's anti-automation posture; the spec's
§13 success criterion mentions LinkedIn specifically but real-world users may
want to start with GitHub or Notion.

### 4. Sliding-window counters, not token buckets

`_Bucket` keeps a `deque` of hit timestamps and rejects new requests when
`len(hits) >= limit` within the window. Equivalent throughput to a token
bucket, simpler invariant ("at most N events in the last W seconds"), and
cleaner to reason about per-session because there's no replenish thread.

Memory bound: `deque(maxlen=4096)` per bucket per session. Three buckets per
session (navigations/min, actions/min, actions/hour) ⇒ ~100 KB worst case.
Acceptable for v1's 1–3-concurrent-session use case.

## Consequences

- **T5** ("Agent acts outside its policy scope inside the granted session")
  moves from Partial → Implemented. Route handler enforces denied paths,
  default action, navigation rate limit. `coral_check_action` lets agents
  pre-flight without burning their action budget.
- **`coral approve` / `coral deny` / `coral reviews list` / `coral policy
  get|put`** CLI commands wire up the operator side of the review flow.
- **Bundled packs** make the §13.1 success criterion ("agent reads my feed
  in under 5 minutes") materially easier — no policy authoring required.

## Accepted limitations

- **Non-blocking review** isn't what some agent frameworks expect. The agent
  that called `coral_request_review` gets a `review_id` and has to choose:
  poll `coral_check_action` after waiting, or abandon. Document.
- **No notification UI** yet. Audit + stderr log only; the extension or a
  webhook surface picks this up later.
- **Action verbs are stringly-typed.** The agent declares `{"type": "post"}`,
  the policy lists `review_required: [{action: post}]`. Typos on either side
  silently miss. A taxonomy registry can come later.
- **Rate-limit memory** scales O(events_in_window) per session. Fine for
  v1; revisit if a session ever sees thousands of events.
- **Per-agent filtering** of session visibility is not in this track. All
  active sessions remain visible to every authenticated MCP client. A
  per-agent allowlist on the policy YAML is queued for v1.x.

## When to revisit

- When a notification surface (extension push, ntfy, plyer) lands → wire
  into `policy.review.requested` events without changing the engine.
- When agents start composing multi-step actions → consider a "batch check"
  MCP tool that pre-flights N actions at once.
- When the spec's "default_allow" call hits real users and we hear demand
  for default-deny everywhere → portfolio-level decision to flip the model
  default.
