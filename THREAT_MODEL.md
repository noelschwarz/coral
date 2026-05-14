# Coral — Threat model

Mirrors **§6 — Security threat model** in [`coral-engineering-spec.md`](./coral-engineering-spec.md). The spec remains authoritative; this file tracks **implementation status** for shipped code.

## 6.1 Assets to protect

1. Captured session state (cookies, storage, etc.)
2. Vault encryption key (passphrase-derived; must not touch disk in plaintext)
3. Daemon API bearer tokens
4. Audit log (local sensitivity / privacy)

## 6.2 Threats and mitigations

| # | Threat | Mitigation (implementation notes) | Status |
|---|--------|-------------------------------------|--------|
| T1 | Malicious local process reads vault file | SQLCipher full-DB encryption; Argon2id-derived key; ADR-006 | **Implemented** — ciphertext pages via SQLCipher; plaintext `vault_meta.json` holds salt + Argon2 parameters only (see ADR-006). |
| T2 | Impostor binds to daemon port | Local bind + bearer-token middleware | **Implemented** — daemon refuses to bind anything other than `127.0.0.1` (`coral.daemon`); every authenticated HTTP route requires `Authorization: Bearer <token>` against a SHA-256 hash stored in `api_tokens` (`coral.auth`); the MCP HTTP transport on `127.0.0.1:8766` uses the same bearer scheme via `MCPBearerAuth` (`coral.mcp_server`). |
| T3 | Non-operator browser completes handshake | Operator-mediated challenge printed on TTY | **Implemented** — challenge is generated per daemon process, printed to TTY only, single-use (consumed on first success), constant-time-compared, and rate-limited to 5 attempts/minute (`coral.http_api.HandshakeState`). |
| T4 | Compromised extension exfiltrates sessions | Least privilege + user education + CORS allowlist + minimal extension permissions | **Implemented for v0.5** — CORS regex restricts the daemon to `chrome-extension://*` origins; the daemon never returns `state_blob` over HTTP (sessions are restored only into Playwright contexts the daemon owns, ADR-010). The Coral extension uses the minimal MV3 permissions (`storage`, `tabs`, `scripting`, `cookies`, `alarms`, `clipboardRead`) and `host_permissions` for `127.0.0.1` + `<all_urls>`. **`clipboardRead`** was added in Track K to support clipboard auto-detect during pairing (ADR-016) — it lets the popup pre-fill the challenge from the clipboard. Tradeoff: one fewer manual step vs. one slightly scarier permission prompt at install. **`<all_urls>`** is required to call `chrome.cookies.getAll({url})` for arbitrary sites the user wants to capture. Extension-extension impersonation is not in scope: Chrome's same-origin policy prevents other extensions from reading our `chrome.storage.local` token. |
| T5 | Agent exceeds policy | Daemon-side enforcement via Playwright routes | **Implemented** — every session gets a route handler that evaluates each navigation through `coral.policy.PolicyEngine`. Denied paths, default-deny, and sliding-window rate limits abort the request before the network call; review-required paths abort with a `policy.review_required` audit row. `coral_check_action` lets agents pre-flight verbs against the same engine. Action verbs and per-agent filtering are still stringly-typed and unfiltered respectively (ADR-011 limitations). |
| T6 | Malicious agent with CDP control exfiltrates | **_Accepted risk / agent trust boundary — document clearly_** | Documented in spec; per-session Chromium (ADR-010) bounds the blast radius to that one session. |
| T7 | Network adversary on localhost | Out of scope per spec | N/A |
| T8 | Orphan Chromium after crash | Graceful shutdown + cross-restart recovery sweep | **Implemented** — daemon's `SessionServer.shutdown()` closes every open context + browser process on SIGTERM. The cross-restart sweep (spec §7.4) is in `coral.sessions.recovery_kill_orphan_browsers`: on every `coral start`, scan `psutil.process_iter` for Chromium processes tagged with `CORAL_DAEMON_HOME=<our home>` env var and kill them. Tested against `psutil` mocks; verified the regex matches macOS / Linux process names. |
| T9 | Offline vault theft | Argon2id tuning + passphrase policy | **Implemented** — minimum 12-character passphrase; production Argon2 parameters in `coral.crypto.PRODUCTION_PARAMS`; resilience depends on SQLCipher packaging (ADR-006). |
| T10 | Stale session replay | Document limitation + kill-on-login policies | **Documented limitation.** Captured cookies remain replay-valid until the site itself rotates them server-side. Per-pack `session.kill_on_redirect_to_login: true` (default in all six bundled packs) detects when the site forces re-auth and tears the session down. Cookie binding (Device-Bound Session Credentials, CHIPS) is the longer-horizon answer — out of scope for v1. |
| T11 | Indirect prompt injection | Out of scope v1; document | N/A — Ammolite's problem per spec §6.4. |

### Self-attack scenarios

What an attacker can / can't do at each capability tier:

| Tier | Capability | What they get |
|---|---|---|
| **A** | Internet access only | Nothing. Daemon binds `127.0.0.1`. |
| **B** | Same machine, different OS user | Nothing reachable from outside the user's `$CORAL_HOME` (file permissions). Cannot read the vault, the `cli.token`, or the PID file. |
| **C** | Same machine, same OS user, no terminal | Can read `$CORAL_HOME/cli.token` if the daemon is running ⇒ has bearer access to every HTTP endpoint. Can list sessions, capture new ones (an active session for an existing origin returns 409), revoke. Cannot get session `state_blob` over HTTP (T4 defense). Can open an MCP session and drive an authenticated browser. **This is the bridge-token risk; equivalent to compromising the user's account.** |
| **D** | Same machine, same OS user, terminal | All of C, plus can read the daemon's stdout for the handshake challenge — i.e. can pair as a new client. Otherwise no additional capability over C. |
| **E** | Offline + vault file copy | Brute-force against the passphrase, gated by `PRODUCTION_PARAMS` Argon2id (~500 ms/attempt). A 12-character passphrase with mixed entropy is impractical to brute-force; a weak passphrase (e.g. dictionary word) is reachable. **Encourage passphrase managers; do not encourage memorization.** |
| **F** | Owns the agent | Equivalent to C *within the session's policy bounds.* Per-session Chromium (ADR-010) prevents cross-session leakage. Policies (ADR-011) constrain navigation + actions; review-required actions block. The agent CAN exfiltrate cookies it has access to — accepted risk T6. |

## 6.3 Cryptographic primitives

- **Argon2id:** `coral.crypto.PRODUCTION_PARAMS` (spec §6.3 alignment); **`coral.crypto.TEST_PARAMS`** for automated tests only.
- **SQLCipher:** `sqlcipher3` bindings; key formatted as raw hex `PRAGMA key` (see `format_sqlcipher_hex_pragma_key`).
- **API tokens:** `generate_token` / `hash_token` / `constant_time_compare` in `coral.crypto`. Extension tokens expire after 24h, CLI bridge tokens after 30d (configurable via `Config`).
- **Audit invariant:** every authenticated request and every authentication failure writes an `audit_log` row. Failure rows record the *reason* (`token_not_found`, `token_expired`, `wrong_challenge`) but never the submitted token, hash, or challenge.

## 6.4 Known limitations of v1

Aligned with spec §6.4 (IndexedDB best-effort, malicious-agent trust model, etc.).
