# ADR-017: OS keychain integration for the vault passphrase

## Status

Accepted — Track L (2026).

## Context

Track K (ADR-016) shipped `coral install-service` so users could daemonize Coral
on macOS and Linux. That track ran into a real tradeoff and punted on it:

- **Default (no `--passphrase-env`):** the service file omits the passphrase,
  so the daemon can't start until the user runs `coral up` manually after every
  reboot. The "auto-start at login" win disappears.
- **`--passphrase-env`:** writes a `CORAL_PASSPHRASE` placeholder into the
  service file. The user edits it to their real passphrase, mode 0600. The
  daemon starts at login but the passphrase sits in plaintext on disk inside a
  file the user remembers writing once and forgets exists.

Neither is good. The right answer is to stash the passphrase in the OS-managed
keychain — Keychain Access on macOS, Secret Service / GNOME Keyring / KWallet
on Linux — which is unlocked automatically when the user logs in. That was
flagged as the "real fix" in ADR-016's "When to revisit" but deferred because
keychain access "breaks the OS-agnostic v1 posture." Track L revisits.

## Decision

### 1. Subprocess wrappers, no new Python deps

`coral/keychain.py` shells out to two stable OS-vendor CLIs:

- **macOS:** `/usr/bin/security` (`add-generic-password`, `find-generic-password`,
  `delete-generic-password`). Always present on macOS.
- **Linux:** `secret-tool` from `libsecret-tools` (Debian/Ubuntu) or `libsecret`
  (Fedora/Arch). Talks to whatever Secret Service implementation is running
  (GNOME Keyring, KWallet). Not always present on minimal/server installs.

Rejected: the `keyring` Python package. It would handle macOS/Linux/Windows
in one API but adds a third-party dependency (plus transitive D-Bus bindings
on Linux) right before the planned external security review. Subprocess
mirrors how `coral install-service` already talks to `launchctl` and
`systemctl` — same trust posture, no new code-signing surface.

### 2. Per-CORAL_HOME accounts

Service name is `coralbridge`; account is `vault-passphrase:<resolved CORAL_HOME>`.
Multiple Coral installs on one user account (separate `--home` dirs, test
isolation) coexist without colliding. Resolving the path is important: a user
who passes `~/.coral` and `/Users/x/.coral` gets one entry, not two.

### 3. `coral install-service` defaults to keychain

The flag matrix becomes:

| flags                       | passphrase storage                              |
|-----------------------------|-------------------------------------------------|
| *(none — default)*          | OS keychain. Verified against the vault first.  |
| `--passphrase-env`          | Placeholder in service file; user edits to real value (Track K behavior, preserved). |
| `--no-keychain`             | No passphrase anywhere; manual `coral up` after reboot (Track K's old default). |

`--passphrase-env` and `--no-keychain` are mutually exclusive (exit 2). The
default flow prompts for the passphrase only when the keychain doesn't already
have one for this CORAL_HOME, and **verifies** the passphrase actually unlocks
the vault before storing — so we never persist a wrong passphrase that would
loop the daemon at startup.

If the platform has no keychain backend (Linux without libsecret-tools, or
Windows), the default flow exits with a clear "re-run with `--passphrase-env`
or `--no-keychain`" message.

### 4. Passphrase resolution order in `coral start`

`_unlock_passphrase_prompt(coral_home)` now resolves in this order:

1. `CORAL_PASSPHRASE` environment variable (unchanged — covers CI, ad-hoc
   manual override).
2. OS keychain entry for `coral_home` (new).
3. Interactive TTY prompt (unchanged for `coral up`).
4. **No TTY and no other source → exit 1 with a clear message** (new — this
   replaces the old behavior where `typer.prompt` would block forever or emit
   an opaque error when launchd/systemd ran the daemon headless).

This is the actual mechanism that makes "daemon starts automatically at login"
work end to end.

### 5. New CLI surface: `coral keychain {store,clear,status}`

Direct management for users who want it without going through
`install-service`. `clear` is idempotent. `store` runs the same
verify-against-vault check as install-service, so storing a wrong passphrase
is rejected at the entry point, not at startup.

`coral uninstall-service` removes the keychain entry by default (use
`--keep-keychain` to preserve it).

## Consequences

- **`coral install-service` is now a one-step setup** that survives reboots
  without secrets on disk. Closes the daily-use gap Track K left.
- **Threat model:** the passphrase moves from "in the user's head + briefly
  in process memory" to "in the user's head + briefly in process memory +
  encrypted at rest in the OS keychain, decrypted automatically at login."
  Net change: an attacker who has the user's logged-in session can now ask
  the OS keychain for the passphrase. They could already read `vault.db` and
  `cli.token` from `$CORAL_HOME` in that same scenario — same trust boundary
  (T1).
- **macOS** passes the passphrase through `argv` (`security -w PASS`). Briefly
  visible to `ps` for the same uid; that's inside T1 already. Documented in
  `coral/keychain.py`.
- **Linux** passes the passphrase through stdin; no argv leak.

## Accepted limitations

- **Linux requires `secret-tool`.** Servers and minimal installs may not have
  it. Users get a clear error message pointing at `apt install libsecret-tools`
  (or distro equivalent), with `--passphrase-env` as the documented fallback.
- **No Windows support in this track.** Mirrors the install-service scope from
  ADR-016. Windows Credential Manager would need a separate path; revisit when
  Windows enters CI.
- **No keychain unlock-on-demand prompt.** If the macOS keychain is locked (rare
  for the user's login keychain, but possible), `security find-generic-password`
  fails and the daemon errors out. We don't pop a Keychain unlock dialog —
  daemons can't.
- **macOS argv leak.** Documented. We could pipe via stdin using a temporary
  AppleScript or `expect`-style PTY but the complexity isn't worth the brief
  same-uid `ps` window.

## When to revisit

- When Windows enters CI: add a Credential Manager backend (`CredRead`,
  `CredWrite`, `CredDelete` via `ctypes`/`pywin32`).
- When the external security reviewer asks: consider whether macOS' `argv`
  passphrase pass-through is worth replacing with stdin even at the cost of
  PTY complexity.
- If users on headless Linux servers complain about `secret-tool` being
  unavailable: consider a Coral-managed encrypted file under `$CORAL_HOME`
  decrypted by a system-keyring-stored key. But that's just moving the problem.
