/**
 * Cookie + storage capture for the active tab's origin (spec §3.1, §4.2).
 *
 * Cookies come from ``chrome.cookies.getAll({url})`` — requires the
 * ``cookies`` permission + host permission for the URL. Storage requires
 * running a small function in the page via ``chrome.scripting.executeScript``;
 * that requires the ``scripting`` permission + host access.
 *
 * IndexedDB and service workers are deliberately skipped for v0.5 per
 * spec §6.4 (best-effort, deferred).
 */

import type { CapturedCookie, StateBlob } from "./client.js";

export function originOfUrl(url: string): string {
  const u = new URL(url);
  if (u.protocol !== "http:" && u.protocol !== "https:") {
    throw new Error(`unsupported scheme for capture: ${u.protocol}`);
  }
  return `${u.protocol}//${u.host}`;
}

export type ChromeCookieLike = chrome.cookies.Cookie;

/** Normalize Chrome's cookie shape into Coral's §4.2 shape. */
export function normalizeCookie(c: ChromeCookieLike): CapturedCookie {
  const out: CapturedCookie = {
    name: c.name,
    value: c.value,
    domain: c.domain,
    path: c.path,
  };
  if (typeof c.expirationDate === "number") {
    out.expires = c.expirationDate;
  }
  if (typeof c.httpOnly === "boolean") out.httpOnly = c.httpOnly;
  if (typeof c.secure === "boolean") out.secure = c.secure;
  switch (c.sameSite) {
    case "strict":
      out.sameSite = "Strict";
      break;
    case "lax":
      out.sameSite = "Lax";
      break;
    case "no_restriction":
      out.sameSite = "None";
      break;
    // "unspecified" → omit
  }
  return out;
}

export async function captureCookiesForUrl(url: string): Promise<CapturedCookie[]> {
  const raw = await chrome.cookies.getAll({ url });
  return raw.map(normalizeCookie);
}

/** Read window.localStorage / sessionStorage inside the page (single tab). */
function readStorageInPage(): {
  local: Record<string, string>;
  session: Record<string, string>;
} {
  const dump = (s: Storage): Record<string, string> => {
    const out: Record<string, string> = {};
    for (let i = 0; i < s.length; i += 1) {
      const k = s.key(i);
      if (k !== null) {
        const v = s.getItem(k);
        if (v !== null) out[k] = v;
      }
    }
    return out;
  };
  return { local: dump(window.localStorage), session: dump(window.sessionStorage) };
}

export async function captureStorageInTab(
  tabId: number,
): Promise<{ local_storage: Record<string, string>; session_storage: Record<string, string> }> {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func: readStorageInPage,
  });
  const first = results[0];
  if (!first || !first.result) {
    return { local_storage: {}, session_storage: {} };
  }
  return {
    local_storage: first.result.local,
    session_storage: first.result.session,
  };
}

/**
 * Build the full §4.2 state_blob for the given tab.
 *
 * The caller is responsible for picking the right tab — usually the active
 * tab in the active window.
 */
export async function captureStateForTab(tab: chrome.tabs.Tab): Promise<{
  origin: string;
  state: StateBlob;
}> {
  if (!tab.id) throw new Error("tab has no id");
  if (!tab.url) throw new Error("tab has no URL (likely a chrome:// page)");
  const origin = originOfUrl(tab.url);
  const cookies = await captureCookiesForUrl(tab.url);
  const storage = await captureStorageInTab(tab.id);
  const userAgent = typeof navigator !== "undefined" ? navigator.userAgent : "unknown";
  const state: StateBlob = {
    version: 1,
    captured_at: Math.floor(Date.now() / 1000),
    user_agent: userAgent,
    origin,
    cookies,
    local_storage: storage.local_storage,
    session_storage: storage.session_storage,
  };
  return { origin, state };
}
