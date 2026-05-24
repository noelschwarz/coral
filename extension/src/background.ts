/// <reference types="chrome" />

/**
 * Coral MV3 service worker.
 *
 * Owns the bearer token (in ``chrome.storage.local``), the auto-refresh
 * alarm, and the popup-RPC handler. The popup is short-lived; everything
 * that needs to outlive a popup close lives here.
 */

import {
  captureStateForTab,
  originOfUrl,
} from "./capture.js";
import { DaemonClient, DaemonError } from "./client.js";
import type { AppState, PopupRequest, PopupResponse } from "./messages.js";
import {
  emptyState,
  isTokenValid,
  loadState,
  saveState,
  shouldRefresh,
  type PersistedState,
  type StorageArea,
} from "./state.js";

const REFRESH_ALARM_NAME = "coral.refresh";
const REFRESH_ALARM_PERIOD_MINUTES = 30;
const CLIENT_NAME = "extension";

function storageArea(): StorageArea {
  return chrome.storage.local;
}

function now(): number {
  return Math.floor(Date.now() / 1000);
}

async function activeTabOrigin(): Promise<string | null> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.url) return null;
  try {
    return originOfUrl(tab.url);
  } catch {
    return null;
  }
}

async function buildState(persisted: PersistedState): Promise<AppState> {
  const client = new DaemonClient(persisted.daemonBase);
  let daemonReachable = false;
  try {
    await client.healthz();
    daemonReachable = true;
  } catch {
    daemonReachable = false;
  }
  const paired = isTokenValid(persisted, now());
  let sessions: AppState["sessions"] = [];
  if (paired && persisted.token && daemonReachable) {
    try {
      const r = await client.listSessions(persisted.token);
      sessions = r.sessions;
    } catch (e) {
      if (e instanceof DaemonError && e.status === 401) {
        // Token rejected; force re-pair.
        await saveState(storageArea(), emptyState());
        return buildState(emptyState());
      }
    }
  }
  return {
    daemonReachable,
    paired,
    tokenExpiresAt: persisted.tokenExpiresAt,
    currentOrigin: await activeTabOrigin(),
    sessions,
    lastError: null,
  };
}

async function handlePair(challenge: string): Promise<AppState> {
  const persisted = await loadState(storageArea());
  const client = new DaemonClient(persisted.daemonBase);
  const res = await client.handshake(challenge, CLIENT_NAME);
  const next: PersistedState = {
    ...persisted,
    token: res.token,
    tokenExpiresAt: res.expires_at,
  };
  await saveState(storageArea(), next);
  await chrome.alarms.create(REFRESH_ALARM_NAME, {
    periodInMinutes: REFRESH_ALARM_PERIOD_MINUTES,
  });
  return buildState(next);
}

async function handleCapture(
  origin: string,
  label: string | undefined,
): Promise<AppState> {
  const persisted = await loadState(storageArea());
  if (!isTokenValid(persisted, now()) || !persisted.token) {
    throw new Error("not paired");
  }
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) throw new Error("no active tab");
  if (!tab.url || originOfUrl(tab.url) !== origin) {
    throw new Error("active tab origin no longer matches the requested origin");
  }
  const { state } = await captureStateForTab(tab);
  const client = new DaemonClient(persisted.daemonBase);
  await client.captureSession(persisted.token, { origin, label, state });
  return buildState(persisted);
}

async function handleRevoke(sessionId: string): Promise<AppState> {
  const persisted = await loadState(storageArea());
  if (!persisted.token) throw new Error("not paired");
  const client = new DaemonClient(persisted.daemonBase);
  await client.revokeSession(persisted.token, sessionId);
  return buildState(persisted);
}

async function handleRefresh(
  sessionId: string,
  origin: string,
): Promise<AppState> {
  const persisted = await loadState(storageArea());
  if (!isTokenValid(persisted, now()) || !persisted.token) {
    throw new Error("not paired");
  }
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) throw new Error("no active tab");
  if (!tab.url || originOfUrl(tab.url) !== origin) {
    // The user's main Chrome isn't on the right site to re-capture state.
    // Surface a specific, actionable error rather than a generic mismatch.
    throw new Error(
      `Navigate to ${origin} in this window and try again — the active tab ` +
        "needs to be on the same origin to refresh the session.",
    );
  }
  const { state } = await captureStateForTab(tab);
  const client = new DaemonClient(persisted.daemonBase);
  await client.refreshSession(persisted.token, sessionId, { origin, state });
  return buildState(persisted);
}

async function handleUnpair(): Promise<AppState> {
  const persisted = await loadState(storageArea());
  await saveState(storageArea(), { ...emptyState(), daemonBase: persisted.daemonBase });
  await chrome.alarms.clear(REFRESH_ALARM_NAME);
  return buildState(emptyState());
}

async function maybeRefreshToken(): Promise<void> {
  const persisted = await loadState(storageArea());
  if (!shouldRefresh(persisted, now()) || !persisted.token) return;
  const client = new DaemonClient(persisted.daemonBase);
  try {
    const refreshed = await client.refresh(persisted.token);
    await saveState(storageArea(), {
      ...persisted,
      token: refreshed.token,
      tokenExpiresAt: refreshed.expires_at,
    });
  } catch (e) {
    if (e instanceof DaemonError && e.status === 401) {
      // Daemon rejected the token (likely restarted with a new challenge).
      await saveState(storageArea(), {
        ...emptyState(),
        daemonBase: persisted.daemonBase,
      });
    }
    // Network errors are non-fatal — the alarm fires again in 30 min.
  }
}

chrome.runtime.onMessage.addListener(
  (msg: PopupRequest, _sender, sendResponse: (r: PopupResponse) => void) => {
    void (async () => {
      try {
        let state: AppState;
        switch (msg.type) {
          case "get_state":
            state = await buildState(await loadState(storageArea()));
            break;
          case "pair":
            state = await handlePair(msg.challenge);
            break;
          case "capture":
            state = await handleCapture(msg.origin, msg.label);
            break;
          case "refresh_session":
            state = await handleRefresh(msg.sessionId, msg.origin);
            break;
          case "revoke_session":
            state = await handleRevoke(msg.sessionId);
            break;
          case "unpair":
            state = await handleUnpair();
            break;
        }
        sendResponse({ ok: true, state });
      } catch (e) {
        const err = e instanceof Error ? e.message : String(e);
        sendResponse({ ok: false, error: err });
      }
    })();
    return true; // keep the response channel open for async work
  },
);

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === REFRESH_ALARM_NAME) {
    void maybeRefreshToken();
  }
});

chrome.runtime.onInstalled.addListener(() => {
  void chrome.alarms.create(REFRESH_ALARM_NAME, {
    periodInMinutes: REFRESH_ALARM_PERIOD_MINUTES,
  });
});

chrome.runtime.onStartup.addListener(() => {
  void chrome.alarms.create(REFRESH_ALARM_NAME, {
    periodInMinutes: REFRESH_ALARM_PERIOD_MINUTES,
  });
});
