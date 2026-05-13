# Loading the Coral extension in Chrome (development)

The extension isn't on the Chrome Web Store yet ([ADR-013](../docs/ADR-013-release-strategy.md)
explains why; tldr: we publish with the v1.0 release alongside PyPI). Load it
unpacked for now.

## One-time setup

```bash
cd extension
npm ci
npm run build
```

That produces `extension/dist/` with the manifest, service worker, popup, and
asset bundle.

## Load it in Chrome

1. Open `chrome://extensions`.
2. Enable **Developer mode** (top right).
3. Click **Load unpacked**.
4. Pick the `extension/dist/` directory.

You should see "Coral" in your extensions list. Pin it from the puzzle-piece
menu so the icon's always visible.

## Pair with a running daemon

In a terminal:

```bash
uv run coral start
```

The daemon prints a 4-group challenge:

```
Extension handshake challenge (paste into the Coral extension popup):

    ABCD-EFGH-JKLM-NPQR
```

In Chrome, click the Coral extension icon → paste the challenge → click
**Pair**. The popup transitions to the paired state and shows your current
tab's origin.

## Capture a session

1. Navigate to a site where you're already logged in.
2. Click the Coral icon → **Capture session**.
3. The popup shows the new session under "Captured sessions" with status
   `active`.

You can also revoke from the popup, or unpair the extension entirely (which
drops the local token; the daemon still has the row until you `coral revoke
<origin>`).

## Reloading after code changes

After `npm run build`, click the reload icon next to "Coral" on
`chrome://extensions`. The service worker restarts and the popup reloads on
next open.

## Troubleshooting

**"Daemon offline"** in the popup → `coral start` is not running, or it's
running but listening on a different port (default `127.0.0.1:8765` is
hard-coded; spec §6.2 T2 requires this).

**"Pair failed: invalid_challenge"** → the challenge expired or was already
consumed (it's single-use per daemon process). Stop and restart `coral start`
and use the freshly-printed challenge.

**"Capture failed: active_session_exists_for_origin"** → the daemon already
has an active session for this origin. Revoke it (popup or `coral revoke
<origin>`) before capturing again.

**The session list is empty after pairing on a new daemon** → expected. The
daemon's vault is per-`$CORAL_HOME`; if you `coral init` a fresh home, the
sessions live there but the extension paired against the old one is now
talking to a different daemon. Re-pair.
