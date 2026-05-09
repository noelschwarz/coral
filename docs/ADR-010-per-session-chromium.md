# ADR-010: One Chromium per session, via persistent context

## Status

Accepted — Track D / Week 2 (2026).

## Context

The engineering spec §3.1 and §7.3 describes the daemon as owning **one Chromium
instance, many contexts**. Each `coral_open_session` would create a lightweight
isolated `BrowserContext` inside the shared browser. CDP would be exposed per
context.

Implementing this discovered two problems:

1. **Playwright's `BrowserContext` has no per-context CDP endpoint.** The
   `--remote-debugging-port` flag exposes one CDP endpoint per Chromium *process*,
   not per context. An agent given the shared Chromium's CDP URL can list every
   target via `Target.getTargets` — including pages from other agents' contexts.
   That breaks the isolation the threat model assumes.
2. **`launch()` + `new_context()` leaves the default browser context alongside
   ours.** When an agent uses `chromium.connect_over_cdp(url)`, `browser.contexts`
   returns *both* the empty default and our restored one. The agent has no
   reliable way to pick the right one — `contexts[0]` is sometimes empty,
   sometimes correct, depending on Chromium's startup ordering. The headline
   e2e test failed exactly this way until we switched.

## Decision

**Each `coral_open_session` launches its own Chromium process via
`launch_persistent_context(user_data_dir=...)` with a per-session temp dir.**

- One Chromium process per session ⇒ one CDP endpoint per session ⇒ natural
  per-agent isolation. `Target.getTargets` only reveals the agent's own context.
- Persistent context ⇒ exactly one `BrowserContext` exists ⇒
  `browser.contexts[0]` is unambiguously the restored context.
- The temp `user_data_dir` is removed when the session closes.

## Consequences

- Memory: ~100–200 MB per concurrent session. Acceptable for v1's 1–3-concurrent
  use case.
- Startup: ~700 ms per session (Chromium cold start), measured against
  the local test server. Acceptable for the human-perceived latency budget.
- The "one Chromium, many contexts" optimization can return in v1.x via a CDP
  proxy that filters `Target.*` calls per agent. Out of scope for v1.

## Accepted limitations

- **Multi-context-per-Chromium is not available to agents.** An agent that
  wanted to drive two captured sessions simultaneously gets two separate CDP
  URLs from two `coral_open_session` calls. They cannot share cookies or
  storage between contexts via `localStorage` postMessage tricks. We consider
  this a feature, not a bug.
- **Headless detection.** Real-world sites can detect Playwright's headless
  mode. Out of scope for v1; users running v1 against bot-defended sites will
  hit this limitation. Headed mode is a future config option.
- **Default `user_data_dir` location is the OS temp dir.** On systems that
  mount `/tmp` with `noexec`, Chromium may fail to launch. Document and let
  users override via env var if needed (post-v1).

## When to revisit

- When the v1.x roadmap demands multi-tenant agent hosting on a single machine
  (memory pressure becomes the bottleneck).
- When a CDP-target-filtering proxy library appears in the ecosystem that does
  per-context isolation cleanly.
- When Playwright ships per-context CDP endpoints upstream.
