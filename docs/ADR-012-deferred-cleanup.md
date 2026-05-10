# ADR-012: Deferred cleanup items (post-Track-E)

## Status

Accepted — Track F (2026).

## Context

Track F shipped a focused cleanup batch (refresh tokens, the audit-write
single source of truth, the CLI HTTP-client extraction, the upgrade path that
seeds bundled packs on `coral start`, the per-session-Chromium launch race).
A few items the post-Track-E audit surfaced are real but deliberately
**not** in scope for Track F because each one carries enough risk that it
deserves its own track. This ADR records them so future contributors don't
re-discover the same questions cold.

## Deferrals

### 1. Vault writer cross-event-loop decoupling

**The problem.** `Vault.initialize` / `Vault.open` start a writer task on the
event loop that called them. The async `asyncio.Queue` and the per-write
`asyncio.Future` are bound to that loop. If another loop later tries to
enqueue a write through the same `Vault` instance, the future is created on
the calling loop but the awaiter (the writer task) lives on the original
loop — the write never completes.

This bit us twice during Tracks B and D:

- `tests/unit/test_auth.py` originally used Starlette's sync `TestClient`,
  which runs requests on a portal loop. The vault was initialized on
  pytest-asyncio's loop. Cross-loop deadlock. Workaround: switched to
  `httpx.AsyncClient` so request handling shares the test loop.
- The MCP HTTP success-path test in `tests/unit/test_mcp_server.py` hits the
  same wall because FastMCP's session manager needs Starlette's lifespan,
  which `TestClient` runs. Documented inline; success path covered
  transitively by the stdio integration test.

**Why we're not fixing it in Track F.** The fix is a real refactor:

- Run the writer task on a dedicated thread with its own loop.
- Replace `asyncio.Queue` with `queue.Queue` (or `janus.Queue`).
- Replace per-write `asyncio.Future` with `concurrent.futures.Future` and
  wrap with `asyncio.wrap_future` at the call site.
- Every existing call site (vault.py:`_enqueue_write` and ~40 vault methods
  that go through it) is touched.

That's a 1-2 day refactor with significant regression risk. Track F's bar
was "small, mechanical, no regression risk."

**When to revisit.** When we add anything else that creates its own loop
(a daemon-side notification listener, a desktop tray app subprocess, a
synchronous webhook handler) the deadlock will surface again. At that point
the fix becomes mandatory.

### 2. Shared-Chromium-per-daemon with CDP target filtering

**The problem.** ADR-010 chose one Chromium per session because Playwright
has no per-context CDP endpoint and `Target.getTargets` over the shared
Chromium's CDP would let one agent enumerate another agent's pages. The
spec §3.1/§7.3 envisioned "one Chromium, many contexts" — we deviate for
correctness.

**Why we're not fixing it in Track F.** A CDP proxy that filters `Target.*`
calls per agent (and handles attach/detach correctly across the rest of CDP)
is a non-trivial piece of code. Memory cost of the per-session approach is
~100-200 MB per concurrent session, which the v1 audience (1-3 concurrent
sessions per machine) can absorb.

**When to revisit.** When agent platforms start hosting >5 concurrent Coral
sessions on shared infrastructure. Until then, the simpler architecture is
the right one.

### 3. Application-layer AES-GCM fallback for SQLCipher

**The problem.** ADR-006 documented the SQLCipher wheel-availability risk
and named the application-layer AES-GCM fallback. The fallback exists on
paper, not in code.

**Why we're not fixing it in Track F.** The SQLCipher path is working on
every platform the team has tested. Building the fallback before it's
needed risks shipping a less-secure path that someone enables "to make CI
faster" or similar.

**When to revisit.** First time a user reports `sqlcipher3` failing to
install. Ship the fallback as a feature-flag, gated on `--vault-mode=app-aes`,
with prominent docs about the metadata leak (table names, row counts,
timestamps visible to disk-level adversaries).

### 4. Notification surface for `coral_request_review`

**The problem.** ADR-011 made `coral_request_review` non-blocking — the
agent gets a `review_id`, the operator decides via `coral approve`. There's
no push to the operator today: they have to `coral reviews list` themselves
or be told out-of-band.

**Why we're not fixing it in Track F.** The right notification surface
depends on what's installed: Chrome extension push, `plyer` system
notification, ntfy/Pushover webhook, or a future tray app. Each has a
different integration story. Adding any one of them without a clear
deployment story risks shipping the wrong abstraction.

**When to revisit.** When the Chrome extension lands a real popup UI (the
out-of-repo track), wire `policy.review.requested` audit events into the
extension's notification surface. That becomes the canonical path; CLI
remains the fallback.

### 5. CLI/daemon coverage measurement across subprocess boundaries

**The problem.** `coral/cli.py` and `coral/daemon.py` show 0% coverage in
`pytest --cov` because the integration tests spawn them as subprocesses.
The actual exercised line count is much higher.

**Why we're not fixing it in Track F.** `coverage run --parallel` plus a
`.coveragerc` `sigterm = true` setting plus a `combine` step works but
adds CI complexity and tooling debt. Track F added in-process unit tests
via `typer.testing.CliRunner` instead (test_cli_status.py +
test_cli_client.py) which cover the testable paths without the subprocess
gymnastics.

**When to revisit.** When someone wants accurate "100% coverage" numbers
for a release announcement. The current numbers are accurate-for-purpose:
modules with security-critical logic are 90%+, and the e2e suite proves
the subprocess paths work.

## Consequences

- The audit's "Top 8 cleanup items" list shrinks to "the four deferred
  items above, by design." Future contributors can find the rationale here
  instead of inferring it from gaps.
- None of these deferrals block v1.0 launch. Each carries a clear
  "when-to-revisit" trigger.

## When to revisit this ADR

When any of the five items above ships, update the corresponding section
with a `Status: Resolved` line and link to the implementing PR.
