/**
 * Persistent token + metadata storage for the background service worker.
 *
 * Lives in ``chrome.storage.local`` so it survives service-worker restarts
 * but is scoped to the extension's storage area (encrypted at rest by
 * Chrome's profile encryption — adequate for a daemon-local short-lived
 * bearer token per spec §5.3).
 */

const STORAGE_KEY = "coral.persisted";

export type PersistedState = {
  token: string | null;
  tokenExpiresAt: number | null;
  daemonBase: string;
};

const DEFAULT_DAEMON_BASE = "http://127.0.0.1:8765";

export function emptyState(): PersistedState {
  return { token: null, tokenExpiresAt: null, daemonBase: DEFAULT_DAEMON_BASE };
}

export type StorageArea = {
  get(key: string): Promise<Record<string, unknown>>;
  set(items: Record<string, unknown>): Promise<void>;
};

export async function loadState(area: StorageArea): Promise<PersistedState> {
  const got = await area.get(STORAGE_KEY);
  const raw = got[STORAGE_KEY];
  if (!raw || typeof raw !== "object") return emptyState();
  const obj = raw as Record<string, unknown>;
  return {
    token: typeof obj.token === "string" ? obj.token : null,
    tokenExpiresAt:
      typeof obj.tokenExpiresAt === "number" ? obj.tokenExpiresAt : null,
    daemonBase:
      typeof obj.daemonBase === "string" ? obj.daemonBase : DEFAULT_DAEMON_BASE,
  };
}

export async function saveState(area: StorageArea, state: PersistedState): Promise<void> {
  await area.set({ [STORAGE_KEY]: state });
}

export function isTokenValid(state: PersistedState, nowSeconds: number): boolean {
  return (
    state.token !== null &&
    state.tokenExpiresAt !== null &&
    state.tokenExpiresAt > nowSeconds
  );
}

/**
 * Should we proactively refresh the token soon?
 *
 * We refresh when less than ``REFRESH_THRESHOLD_SECONDS`` remains on the
 * current token. Default: 1 hour — comfortable margin under the daemon's
 * 24-hour extension-token TTL.
 */
export const REFRESH_THRESHOLD_SECONDS = 60 * 60;

export function shouldRefresh(state: PersistedState, nowSeconds: number): boolean {
  if (!isTokenValid(state, nowSeconds)) return false;
  if (state.tokenExpiresAt === null) return false;
  return state.tokenExpiresAt - nowSeconds < REFRESH_THRESHOLD_SECONDS;
}
