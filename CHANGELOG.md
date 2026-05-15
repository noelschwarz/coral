# Changelog

All notable changes to **coralbridge** (Python distribution; import name
`coral`) are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
in spirit — see [ADR-013](docs/ADR-013-release-strategy.md) for the
explicit 4-gate bar for `1.0.0`.

## [Unreleased]

## [0.6.0] — 2026-05

The "ready to publish" release. Architectural cleanup around vault
threading, daily-use UX overhaul, headless-friendly passphrase storage
in the OS keychain, and the public-launch hygiene needed to ship under
Apache-2.0.

### Added

- **OS keychain integration** (Track L, [ADR-017](docs/ADR-017-keychain-integration.md)):
  `coral install-service` now stashes the vault passphrase in the macOS
  Keychain or Linux Secret Service by default, so the daemon can start
  headless at login with no secrets on disk. New `coral keychain
  store|clear|status` commands for direct management. Fallbacks
  preserved as `--passphrase-env` and `--no-keychain`. The passphrase
  resolution order in `coral start` is now: env → keychain → TTY →
  fail-fast (no more silent hangs under launchd / systemd).
- **`coral up`** (Track K, [ADR-016](docs/ADR-016-ux-overhaul.md)):
  one-command setup that initializes the vault, starts the daemon
  detached, and copies the pairing challenge to the system clipboard.
  Detects existing daemons and re-emits the challenge instead of
  failing.
- **`coral install-service` / `coral uninstall-service`** (Track K):
  user-level service install for macOS (launchd LaunchAgent) and Linux
  (`systemd --user`). Default flow stores the passphrase in the OS
  keychain (Track L); `--passphrase-env` writes a placeholder; `--no-
  keychain` requires manual `coral up` after reboot.
- **Clipboard handshake** (Track K): the extension popup auto-detects
  the pairing challenge from the clipboard.
- **Apache-2.0 license** (OSS Phase 1): replaces the prior MIT
  intention from the README footer. `LICENSE` file added; `pyproject.toml`
  uses PEP 639 SPDX metadata.
- **`CODE_OF_CONDUCT.md`** (OSS Phase 1): Contributor Covenant 2.1
  adopted by reference. Reports route through GitHub private
  vulnerability reporting.
- **GitHub plumbing** (OSS Phase 2): issue templates (bug, feature),
  config disabling blank issues with contact links for security and
  Discussions, PR template with test-plan + threat-model + DCO
  checklist, `CODEOWNERS`, weekly Dependabot for pip / npm
  (extension) / github-actions.

### Changed

- **Vault runs in a dedicated worker thread** (Track J',
  [ADR-015](docs/ADR-015-vault-worker-thread.md)): SQLCipher and
  Argon2id no longer block the asyncio loop. All vault operations
  marshal through a queue to a single owner thread. Eliminates the
  "second request hangs while the first is decrypting" failure mode
  observed in load tests.
- **README**: status banner ("alpha — pre-audit") at the top; license,
  Python, CI, and status badges; Community section pointing at
  Discussions, Issues, and SECURITY.md; ADR range bumped to 017;
  Demo section with placeholder for a forthcoming screencast.
- **CONTRIBUTING**: DCO sign-off policy with concrete `git commit -s`
  and `git rebase --signoff` recipes; CoC reference; license updated to
  Apache-2.0.
- **SECURITY**: GitHub private vulnerability reporting is the
  canonical channel pre-1.0; stale `security@coralbridge.dev` TODO line
  removed.

### Fixed

- **Headless daemon startup**: `coral start` no longer hangs forever
  when launched by launchd / systemd without a TTY and no passphrase
  source — it now exits with a clear error message pointing at
  `coral keychain store` and `CORAL_PASSPHRASE`.

## [0.5.0] — 2026-05

The "daemon + CLI + extension end-to-end" release. Two of the four
v1.0 gates close in this version (extension functional, §13.1 path
demoable).

### Added

- **Chrome extension** (`extension/`, Track H): real MV3 capture flow.
  Pairs with the daemon, captures cookies + localStorage + sessionStorage
  for the active tab's origin, lists captured sessions, revokes. Tokens
  persist in `chrome.storage.local`; auto-refresh via `chrome.alarms`.
  See `extension/INSTALL.md`.
- **Orphan-process recovery** (Track G, spec §7.4): `coral start` scans
  for Chromium processes tagged with `CORAL_DAEMON_HOME=<our home>` env
  var and kills survivors from a previous crashed daemon. Tagged-only
  matching means other Coral daemons and regular Chrome are untouched.
- **macOS CI matrix** (Track G): adds `macos-latest` alongside
  `ubuntu-latest`. Windows still pending (gated on the AES-GCM fallback
  per ADR-012).
- **Performance baseline** (Track G, `docs/performance.md` +
  `tests/manual/perf_baseline.py`): documented numbers for daemon
  startup, session lifecycle, vault throughput, test-suite execution.
  Not a CI gate per spec §8.4.
- **`CONTRIBUTING.md`, `docs/architecture.md` rewrite, full `README.md`
  rewrite** (Track G).
- **`SECURITY.md`, `CHANGELOG.md`, release workflow, `docs/security-review-prep.md`**
  (Track I — this release).
- **`coral diagnose` CLI** (Track I): self-test for install / permissions /
  daemon state.

### Changed

- **Distribution name** `coral` → `coralbridge` (Track G). The import
  name `coral` stays put. `pip install coralbridge`.
- **`coral start` now seeds bundled behavior packs** (Track F): idempotent;
  upgrade path for pre-Track-E vaults.
- **THREAT_MODEL T4 → Implemented for v0.5** (Track H): minimal MV3
  permissions, `<all_urls>` tradeoff documented, no state_blob in API
  responses.
- **THREAT_MODEL T5 → Implemented** (Track E): policy engine wired into
  the route handler. Denied paths abort with `ERR_BLOCKED_BY_CLIENT`
  before the network call.
- **THREAT_MODEL T8 → Implemented** (Track G): orphan-Chromium sweep.
- **THREAT_MODEL T10 → Documented limitation** (Track G): per-pack
  `kill_on_redirect_to_login` plus a forward pointer to DBSC / cookie
  binding.

## [0.4.x] — 2026-05 (pre-publish)

These versions existed as merged PRs against `main` but were never
published. Recorded here for completeness.

### 0.4.0 — Track F: cleanup before launch

- `coral/audit.py` becomes the single canonical audit-write path
  (every audit row now flows through `write_audit_row`).
- `coral/cli_client.py` (new): HTTP-API client helpers extracted from
  the CLI. Unit-testable via Vitest-style mocked `urlopen`.
- `_compress_blob` / `_decompress_blob` renamed to public
  `compress_blob` / `decompress_blob` (Track A's underscore convention
  was lying — they were used across three modules).
- `SessionServer.open()` race fixed: `_launch_lock` serializes the
  port-pick → Chromium-launch handoff.
- ADR-012 documents 5 deferred items with "when to revisit" triggers.

### 0.3.0 — Track E: policy engine + review flow (Week 3)

- `coral/policy.py`: YAML loader (Pydantic, `extra: forbid`),
  `PolicyEngine` with allow/deny/review_required semantics, sliding-
  window rate limiter.
- `pending_reviews` table (migration `002`) + `coral approve|deny`
  + `coral reviews list` CLI commands.
- `coral_check_action` and `coral_request_review` MCP tools.
- Six bundled behavior packs (GitHub, Gmail, Linear, LinkedIn, Notion,
  Slack) seeded on `coral init` with conservative `default_action: deny`
  postures.
- 24 unit + 2 Hypothesis property-based tests on the engine.

### 0.2.0 — Track D: session restoration + CDP exposure (Week 2)

- `coral/sessions.py`: real `SessionServer` with per-session Chromium
  via `launch_persistent_context` (ADR-010). Max-duration auto-close.
- `coral/restoration.py`: cookies + localStorage + sessionStorage
  restoration into a Playwright context.
- `coral_open_session` and `coral_close_session` MCP tools.
- E2E test proves the agent gets a working authenticated browser.

### 0.1.x — Tracks A, B, daily-use UX

- Vault with SQLCipher + Argon2id (Track A).
- HTTP API + MCP scaffold + bearer auth + CORS (Track B).
- `POST /auth/refresh`, `coral panic`, `coral status`, `coral audit`,
  structured stderr logging (daily-use UX track, ADR-009).

[Unreleased]: https://github.com/noelschwarz/coral/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/noelschwarz/coral/releases/tag/v0.6.0
[0.5.0]: https://github.com/noelschwarz/coral/releases/tag/v0.5.0
