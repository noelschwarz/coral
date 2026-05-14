# ADR-016: UX overhaul — `coral up`, clipboard handshake, service install

## Status

Accepted — Track K (2026).

## Context

User feedback after Track H: the setup is "very bad." Real user words. The
flow asked for:

1. `git clone` the repo
2. `uv sync --all-extras`
3. `uv run playwright install chromium`
4. `uv run coral init` (passphrase prompt)
5. `uv run coral start` (terminal stays open, prints challenge)
6. Open a terminal #2
7. `cd extension && npm ci && npm run build`
8. Open `chrome://extensions`, enable Developer mode, **Load unpacked**
9. Pin the extension
10. Click the icon → paste challenge from terminal #1 into the popup
11. Navigate to a site → click **Capture**

Eleven steps before they have a captured session. Each one a place to bounce.
Most users get lost at #5 ("why does the terminal need to stay open?") or
#10 ("which terminal? this challenge?").

The thesis test is good (`tests/e2e/...` passes; the agent really does drive
your authenticated browser). The onboarding is what was killing adoption.

## Decisions

### 1. `coral up` collapses init + start + clipboard

A single CLI command that:

- Initializes the vault if it doesn't exist (prompts for the passphrase).
- Starts the daemon (detached, via `subprocess.Popen(..., start_new_session=True)`).
- Reads the handshake challenge from `$CORAL_HOME/.pairing_challenge`
  (mode 0600, written by the daemon on every startup).
- Copies the challenge to the system clipboard (`pbcopy` / `xclip` / `wl-copy` /
  `xsel` / `clip`).
- Prints a clear "next step" block with `coral status / list / stop /
  install-service` pointers.

`coral start` (foreground, no clipboard) stays as the underlying
implementation — `coral up` is a friendlier wrapper. Integration tests that
spawn `coral start` directly are unaffected.

### 2. Clipboard auto-detect in the popup

The extension now declares the `clipboardRead` MV3 permission. When the popup
opens in the "unpaired" state, it calls `navigator.clipboard.readText()`,
checks the value against the challenge regex, and if it matches:

- Pre-fills the input.
- Shows a green hint: "Detected challenge from clipboard. Click Pair to continue."
- Moves focus to the **Pair** button.

The user's path becomes: open popup → press Enter → done. Two key presses
instead of "paste from where? oh, that terminal. let me find it. now copy.
switch back. paste. click."

**Trust-model note.** The `clipboardRead` permission is a Chrome-reviewed
permission and may give some users pause at install. Tradeoff: one fewer
manual step vs. one slightly scarier prompt at install. We picked the UX win;
documented in `THREAT_MODEL.md` T4.

### 3. `coral install-service` for daily-driver users

Writes a `launchctl` LaunchAgent on macOS (`~/Library/LaunchAgents/dev.coralbridge.daemon.plist`)
or a `systemd --user` unit on Linux (`~/.config/systemd/user/coralbridge.service`),
then runs the OS-specific load + enable + start. After this, the daemon
starts automatically at login. No terminal needed.

The passphrase question: we deliberately do **not** put the passphrase in
the unit file by default. The user has two paths:

- **Default (no `--passphrase-env`).** The service file omits the passphrase.
  The daemon will fail to start until the user provides it (e.g. via `coral
  up` after each reboot). Safer; less convenient.
- **`--passphrase-env`.** The service file gets a `CORAL_PASSPHRASE=__SET_THIS__`
  placeholder. User must edit the file before the service can start. Mode
  0600 protects against other-user reads but root and same-user file-readers
  can see it. Convenient; less safe. Documented in the help text.

OS keychain integration (the *real* fix) stays deferred per ADR-009 —
that's a v1.x decision because it breaks the "OS-agnostic" v1 posture.

Windows isn't in scope for this track. Documented; users on Windows run
`coral up` manually or use Task Scheduler.

### 4. Better error messages in the popup

The popup used to display whatever raw error string the daemon returned
("invalid_challenge", "rate_limited", "missing_authorization"). Now each
opaque code is mapped to an actionable English sentence. The map lives in
`extension/src/popup.ts:humanizeError`. Examples:

- `invalid_challenge` → "That challenge is no longer valid. Run `coral up`
  in your terminal to get a fresh one (it'll be copied to your clipboard
  automatically)."
- `active_session_exists_for_origin` → "You already have an active session
  for this origin. Revoke it first, or just use it."
- `audit_log_write_failed` → "Coral couldn't write to its audit log — likely
  a corrupted vault or disk full. Check `coral diagnose` in your terminal."

### 5. README rewrite to match

The 11-step instructions become 3 steps:

```sh
git clone …
cd coral && uv sync --all-extras && uv run playwright install chromium
uv run coral up                          # writes the challenge to your clipboard
# Then: load extension/dist/, click the icon, press Enter to pair.
```

## Consequences

- **The headline path is 3 commands + 1 click**, not 11 commands + multiple
  paste operations.
- **The terminal can close** after `coral up` if the user has set the
  service up — `coral install-service` lands in the same track for that
  reason.
- **Error states stop being cryptic.** Every documented daemon error string
  now has a popup-side human translation.
- **`clipboardRead` is a new MV3 permission.** Chrome shows it at install
  time. THREAT_MODEL T4 is updated to document the tradeoff explicitly.

## Accepted limitations

- **No Chrome Web Store publish in this track.** Still requires Developer
  mode + Load unpacked. That's the real bottleneck for non-technical users
  but it's its own track (depends on the user submitting to the store).
- **Daemonized `coral up` only works on POSIX** (uses `start_new_session=True`
  which is a Unix-specific flag for fork+setsid). Windows users get
  `coral up --foreground` and run `coral start` manually.
- **Service install with `--passphrase-env` writes the passphrase to disk.**
  Documented in the help text; ADR-009 deferral covers the keychain fix.
- **The `.pairing_challenge` file is sensitive.** Mode 0600 protects against
  other-user reads. An attacker with read access to `$CORAL_HOME` can see
  the challenge — but they could already see the vault, the cli.token, and
  everything else, so this is already T1's accepted-risk territory.

## When to revisit

- When the extension ships on the Chrome Web Store: the load-unpacked step
  goes away entirely. README becomes 2 commands + 1 install button.
- When OS keychain unlock lands (ADR-009 follow-up): the `--passphrase-env`
  tradeoff disappears.
- When Windows enters CI (paired with the AES-GCM fallback per ADR-012 #3):
  add a Windows path to `coral install-service` (Task Scheduler or a
  Service Control Manager hook).
- If user reports come in complaining about the `clipboardRead` prompt:
  consider a permission-on-demand pattern (request only when needed) or
  drop the auto-detect feature for that user via a setting.
