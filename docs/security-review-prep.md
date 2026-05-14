# Security review prep — Coral v0.5.0

**Purpose.** This document is the briefing a security reviewer should read
*before* opening the code. It scopes the review, points at the critical
paths, summarizes the threat model, and lists what's accepted-risk vs.
in-scope. Bug-bounty-style reports during or after the review go through
the flow in [`SECURITY.md`](../SECURITY.md).

The authoritative threat model is [`THREAT_MODEL.md`](../THREAT_MODEL.md);
this doc complements it with code pointers and a focused checklist.

## Tldr

Coral is a local-first daemon + Chrome extension that lets AI agents
borrow a user's authenticated browser sessions. The high-value asset is
captured session state (cookies + storage) stored in a passphrase-
encrypted SQLCipher vault. Tokens, audit log, and policy decisions are
the supporting machinery.

Trust boundaries:

- Daemon ↔ extension: bearer token, CORS, single-use handshake.
- Daemon ↔ MCP agent: bearer token (HTTP); per-process trust (stdio).
- Daemon ↔ Playwright Chromium: CDP URL handed to the agent, per-session
  Chromium for isolation.
- Vault ↔ disk: SQLCipher encryption at rest, Argon2id-derived key.

What we explicitly **do not** defend against:

- Same-OS-user adversaries with shell access (T1 / T6 / "Self-attack
  Tier C+" in `THREAT_MODEL.md`).
- Malicious agents granted a session (T6 — agent trust is your problem).
- Indirect prompt injection from page content (T11 — Ammolite's problem).
- Network adversaries on localhost (T7 — daemon binds 127.0.0.1).

## Versioning + scope

| Item | Value |
|---|---|
| Repo | `noelschwarz/coral` |
| Commit under review | The HEAD of the `main` branch at review-start |
| Distribution | `coralbridge` on PyPI (not yet published; see ADR-013) |
| Python version | 3.11.15 reference; 3.11+ supported |
| Chrome version | Manifest V3; tested on stable Chromium 141+ |
| Cryptography | SQLCipher 4 (AES-256-CBC + HMAC-SHA512); Argon2id; SHA-256 token hashing; `secrets`-based tokens |

## Critical paths to focus on

Ordered by sensitivity. The reviewer is encouraged to spend most of the
time here.

### 1. Vault encryption (`coral/vault.py`, `coral/crypto.py`)

- **What to verify.** Argon2id parameters match spec §6.3 in production
  (`PRODUCTION_PARAMS`: m=64 MB, t=3, p=4); `TEST_PARAMS` is test-only.
  Key never persists to disk in plaintext. Plaintext `vault_meta.json`
  carries only the salt + parameters (not secret). Wrong-passphrase
  attempts produce `VaultLockedError` with no information about the
  passphrase.
- **Where to look.** `coral/crypto.py:derive_key`, `coral/vault.py:Vault.open`,
  `coral/migrations/001_initial.sql` (vault_metadata table layout),
  `tests/unit/test_vault.py` (round-trip, wrong-passphrase, encrypted-
  meta-tamper).

### 2. Bearer-token middleware (`coral/auth.py`, `coral/mcp_server.py`)

- **What to verify.** Constant-time compare on the token side of the
  handshake (the digest goes through `secrets.compare_digest` via the
  vault lookup; not the path you'd think — review carefully). Failed
  auth never logs the token or its hash, only the reason. Tokens are
  stored only as SHA-256 hex; raw token leaves the daemon exactly once
  (handshake response).
- **Where to look.** `coral/auth.py:require_auth`, `coral/mcp_server.py:MCPBearerAuth`,
  `coral/audit.py:write_audit_row` (the canonical write path).
  `tests/unit/test_auth.py`, `tests/unit/test_mcp_server.py`.

### 3. Single-use handshake (`coral/http_api.py:handshake`)

- **What to verify.** Challenge consumed atomically on success
  (`state.consumed = True`); subsequent attempts return 401 regardless
  of correctness. Rate limit (5/min) applies to all attempts, not just
  failures. Challenge never appears in logs or response bodies.
  Constant-time challenge compare.
- **Where to look.** `coral/http_api.py:handshake` + `HandshakeState`.
  `tests/unit/test_http_api.py::test_handshake_*`.

### 4. CORS posture (`coral/http_api.py`)

- **What to verify.** No `Access-Control-Allow-Origin: *` anywhere.
  Allowlist regex `^chrome-extension://[A-Za-z0-9_-]+$` is precise —
  it does not match `https://attacker-extension.example`. Localhost
  web origins (`http://localhost:3000`) are blocked.
- **Where to look.** `coral/http_api.py:_build_app` (CORS middleware).
  `tests/unit/test_http_api.py::test_preflight_*`.

### 5. Policy engine (`coral/policy.py`)

- **What to verify.** Denied paths checked before allowed paths.
  Rate-limit counters fire even when policy would otherwise allow. No
  path bypasses (the route handler installs `**/*`, not a narrower
  pattern). YAML schema rejects unknown fields (`extra: forbid`).
  `default_action: deny` works as the safer posture (all six bundled
  packs use it).
- **Where to look.** `coral/policy.py:PolicyEngine.evaluate_navigation`,
  `coral/sessions.py:_install_route_handler`. `tests/unit/test_policy.py`
  has 24 unit + 2 Hypothesis property tests; `tests/integration/test_policy_enforcement.py`
  verifies `ERR_BLOCKED_BY_CLIENT` against real Chromium.

### 6. Per-session Chromium isolation (`coral/sessions.py`, ADR-010)

- **What to verify.** `launch_persistent_context(user_data_dir=<tempdir>)`
  per session — not a shared browser. The agent's `connect_over_cdp(url)`
  exposes exactly one context per session; no enumeration of other
  agents' contexts via `Target.getTargets`. Temp directories cleaned up
  on close.
- **Where to look.** `coral/sessions.py:SessionServer.open`, ADR-010 for
  the rationale.

### 7. Audit log discipline

- **What to verify.** Every authenticated path writes an audit row.
  Failure rows record the *reason* only — search the codebase for any
  place where a token, challenge, or passphrase might end up in a
  `detail` payload. `coral/audit.py:write_audit_row` is the only call
  site for `vault.insert_audit`; everything else goes through it.
- **Where to look.** `coral/audit.py` is canonical. Greps for
  `insert_audit` and `audit_log` to verify no bypass paths exist.

### 8. Extension manifest + token storage (`extension/`)

- **What to verify.** MV3 permissions are exactly `storage tabs scripting
  cookies alarms` — no more. `host_permissions` is the minimum needed
  (`127.0.0.1/*` + `<all_urls>` for cookie capture). Token in
  `chrome.storage.local` (not `sync`, not `session`). No content scripts
  injected into arbitrary sites.
- **Where to look.** `extension/public/manifest.json`,
  `extension/src/state.ts`, `extension/INSTALL.md`.

### 9. CLI token file (`coral/daemon.py`)

- **What to verify.** `$CORAL_HOME/cli.token` written with mode `0600`.
  Removed on graceful shutdown. Token rotated per daemon process (each
  `coral start` mints a new one). Documented as the bridge for `coral
  list / audit / panic` CLI commands.
- **Where to look.** `coral/daemon.py:_provision_cli_token`, ADR-009.

## Out-of-scope items (don't waste review time here)

These are documented accepted-risks or out-of-spec for v1:

- **Indirect prompt injection** (T11). Coral does not inspect page content.
- **Malicious agents** with valid CDP access to a granted session (T6).
- **Same-OS-user attacks** (T1 with read access to `$CORAL_HOME`).
- **Network attackers on localhost** (T7).
- **DBSC, CHIPS, cookie-binding** bypass on captured sessions (T10
  documented limitation).
- **The Python supply chain** — report `playwright`, `sqlcipher3`,
  `cryptography`, `mcp`, FastAPI vulnerabilities upstream.
- **Windows-specific issues.** Windows is not in our CI matrix per ADR-013.

## What I want feedback on

If you can prioritize, these are the questions I'd most like an outside
opinion on:

1. **Is the single-use-handshake-by-process model good enough?** The
   challenge is consumed on first successful HTTP call. A malicious
   local process racing the extension would have to (a) read the
   terminal somehow, (b) win the race. Plausible attack scenarios?
2. **Bridge token (`cli.token`) trust boundary.** Anyone with read
   access to `$CORAL_HOME` gets bearer access to every HTTP endpoint.
   Documented in T1; acceptable for v1?
3. **Per-action consent UI is non-blocking.** `coral_request_review`
   doesn't block the MCP tool call; the agent polls. ADR-011 documents
   the tradeoff. Concerning for the agent-trust story?
4. **`<all_urls>` host permission on the extension.** Required to call
   `chrome.cookies.getAll({url})` for arbitrary captures. Less invasive
   alternatives?
5. **No CSP on the popup.** Defaults are fine but worth confirming.
6. **PRAGMA key ordering on SQLCipher.** Verified the key PRAGMA fires
   before any other statement on the connection; please verify the
   sqlcipher3 wrapper does what we think.

## How to reproduce a clean install for review

```bash
git clone https://github.com/noelschwarz/coral
cd coral
uv sync --all-extras
uv run playwright install chromium
uv run coral init          # CORAL_PASSPHRASE env var for non-interactive
uv run coral diagnose      # self-check; should report no fail markers
uv run pytest              # full Python suite
cd extension
npm ci
npm test                   # Vitest unit tests
npm run build              # produces extension/dist/
```

For an end-to-end live exercise:

```bash
# Terminal 1
uv run coral start

# Terminal 2 — note the challenge from terminal 1's output
TOKEN=$(curl -s -X POST http://127.0.0.1:8765/auth/handshake \
  -H 'Content-Type: application/json' \
  -d "{\"challenge\":\"<paste>\",\"client_name\":\"reviewer\"}" | jq -r .token)
curl -s -X POST http://127.0.0.1:8765/sessions \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"origin":"https://example.com","state":{"version":1,"cookies":[]}}'
uv run coral list
uv run coral audit --limit 20
```

## Disclosure

This document and the threat model are public. The reviewer's findings,
the bug-bounty correspondence, and any zero-days reported via the
GitHub Security Advisories flow are private until a fix ships.

Once Coral v1.0 publishes, this document gets a v1.0 follow-up that
references the security review's findings and the mitigations applied.
