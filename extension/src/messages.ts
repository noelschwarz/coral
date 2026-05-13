/**
 * Typed RPC protocol between the popup and the background service worker.
 *
 * The popup is short-lived (gets unloaded as soon as it loses focus). All
 * actual work — fetching, token storage, alarms — lives in the background.
 * The popup sends a ``PopupRequest`` and renders whatever ``PopupResponse``
 * comes back.
 */

export type SessionListItem = {
  id: string;
  origin: string;
  label: string | null;
  created_at: number;
  last_used_at: number | null;
  expires_at: number | null;
  status: "active" | "expired" | "revoked";
};

export type AppState = {
  /** ``true`` once we successfully reached the daemon's /healthz this run. */
  daemonReachable: boolean;
  /** ``true`` once we hold a non-expired bearer token. */
  paired: boolean;
  /** Unix seconds, or ``null`` when not paired. */
  tokenExpiresAt: number | null;
  /** The active tab's origin (``"https://example.com"``), or ``null``. */
  currentOrigin: string | null;
  /** Cached on each ``get_state``; the popup uses it for the list view. */
  sessions: SessionListItem[];
  /** Last error message surfaced to the user, or ``null``. */
  lastError: string | null;
};

export type PopupRequest =
  | { type: "get_state" }
  | { type: "pair"; challenge: string }
  | { type: "capture"; origin: string; label?: string }
  | { type: "revoke_session"; sessionId: string }
  | { type: "unpair" };

export type PopupResponse =
  | { ok: true; state: AppState }
  | { ok: false; error: string };

export const DEFAULT_DAEMON_BASE = "http://127.0.0.1:8765";
