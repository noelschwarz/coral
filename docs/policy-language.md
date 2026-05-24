# Policy language

Authoritative grammar: [`coral-engineering-spec.md`](../coral-engineering-spec.md) §4.3.
Implementation: [`coral/policy.py`](../coral/policy.py).
Design decisions: [`docs/ADR-011-policy-engine.md`](./ADR-011-policy-engine.md).

A policy is a YAML document stored per-origin in the `policies` table. Six
default packs ship in `coral/behavior_packs/` and are seeded on `coral init`.

## Shape

```yaml
origin: https://example.com         # required, http(s) origin only
default_action: deny                # "allow" (spec default) or "deny"
allowed_paths:                      # globs evaluated AFTER denied_paths
  - /feed/*
  - /in/*
denied_paths:                       # globs evaluated FIRST; match → deny
  - /settings/*
denied_actions:                     # action verbs the agent must not call
  - delete_account
review_required:                    # action verbs that need operator approval
  - action: post_content
  - action: send_message
rate_limits:
  navigations_per_minute: 60
  actions_per_minute: 30
  actions_per_hour: 500
session:
  max_duration_minutes: 60
  kill_on_redirect_to_login: true  # see note below
```

Unknown top-level fields are rejected (`extra: forbid` on the Pydantic model).
Field omissions fall back to sensible defaults — only `origin` is mandatory.

> **Note on `kill_on_redirect_to_login`.** The field is preserved for
> backwards-compatibility with shipped behavior packs, but the semantic
> changed in [ADR-018](ADR-018-storage-writeback.md) / PR N3: when a
> same-origin 401 is detected mid-session, the daemon **flags the
> session for user attention** (visible in the extension popup) instead
> of tearing it down. The user clicks Refresh to re-capture from their
> main Chrome and clear the flag.

## Decision order

### Navigation (one URL per request)

1. **Denied paths.** Any glob match → `deny`. Audit `policy.deny`.
2. **Rate limit.** `navigations_per_minute` exceeded → `deny`. Audit `policy.deny`.
3. **Allowed paths.** Any glob match → `allow`. Audit `navigation`.
4. **Default action.** Falls through to `default_action`.

### Action (verb declared by the agent)

1. **`denied_actions`.** Listed → `deny`.
2. **`review_required`.** Listed → `review_required` (agent calls `coral_request_review`).
3. **Rate limit.** `actions_per_minute` or `actions_per_hour` exceeded → `deny`.
4. **Default action.**

## Path globs

Standard `fnmatch` semantics: `*` matches any character except `/`-aware
greedy, `?` matches one character, `[abc]` character classes. The URL's
query string is appended to the path before matching, so a glob like
`/search?*` matches `/search?q=…` but not bare `/search`.

## CLI

```bash
coral policy get https://github.com           # prints YAML
coral policy put https://github.com -f my.yaml
coral reviews list                            # pending reviews
coral approve <review_id>
coral deny    <review_id>
```

All five commands route through the daemon's HTTP API (`/policies/{origin}`
and `/reviews`) using the `cli.token` bridge written by `coral start`.

## MCP integration

- `coral_open_session` returns a `policy_summary` in its response so the
  agent can avoid pre-flighting verbs it already knows are denied.
- `coral_check_action` evaluates a verb against the session's policy and
  consumes one rate-limit slot. Returns `{decision, reason}`.
- `coral_request_review` records a pending review and returns
  `{review_id, status: "pending"}` immediately (non-blocking — see ADR-011).
  Agents poll or abandon.

### Polling pattern for non-blocking review

The agent's recommended loop:

```python
res = await session.call_tool("coral_request_review", {
    "session_handle": handle,
    "action": {"type": "post_content", "target": "/feed/"},
})
review_id = res.structuredContent["review_id"]

# Tell the human what's happening (e.g. via the chat the agent is in).
# Then poll on a sensible cadence — the operator has to switch terminals.

import asyncio
for _ in range(120):  # up to ~10 minutes
    await asyncio.sleep(5)
    check = await session.call_tool("coral_check_action", {
        "session_handle": handle,
        "action": {"type": "post_content"},
    })
    decision = check.structuredContent["decision"]
    if decision in {"allow", "deny"}:
        break
```

The operator decides via `coral approve <review_id>` / `coral deny <review_id>`.
After they decide, the agent's next `coral_check_action` returns `allow` or
`deny` (the policy engine consults the stored decision before consulting the
YAML).

Note (v1 limitation): the engine doesn't yet consult `pending_reviews` rows
when re-checking — the agent has to know what was approved. Wire this in v1.x
when the per-review decision is needed for resume-and-continue flows.

## What's NOT in the language yet

- **Per-agent filtering.** A policy applies to every agent that opens the
  session. Adding `agent_allowlist` / `agent_denylist` is v1.x.
- **Time-of-day restrictions.** No `business_hours_only` field.
- **Action parameter inspection.** Policies act on action *types*, not
  bodies. A policy can deny `send_message` but can't deny
  `send_message where target == 'ceo@…'`.
- **Cross-origin link gating.** When a page on origin A links to origin B,
  the navigation evaluates B's policy independently. There's no
  "navigation policy chain" yet.
