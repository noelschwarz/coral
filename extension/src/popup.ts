/// <reference types="chrome" />

import "./popup.css";

import type { AppState, PopupRequest, PopupResponse, SessionListItem } from "./messages.js";

/** Matches the four-group handshake challenge ``XXXX-XXXX-XXXX-XXXX``. */
const CHALLENGE_RE = /^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$/;

async function rpc(request: PopupRequest): Promise<PopupResponse> {
  return chrome.runtime.sendMessage(request);
}

function $<T extends HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing element: ${id}`);
  return el as T;
}

function show(id: string, visible: boolean): void {
  $(id).hidden = !visible;
}

function setError(msg: string | null): void {
  show("error-section", msg !== null);
  if (msg !== null) {
    $<HTMLParagraphElement>("error-text").textContent = humanizeError(msg);
  }
}

/** Map opaque daemon errors to clear, actionable user-facing messages. */
function humanizeError(raw: string): string {
  const map: Record<string, string> = {
    invalid_challenge:
      "That challenge is no longer valid. Run `coral up` in your terminal " +
      "to get a fresh one (it'll be copied to your clipboard automatically).",
    rate_limited:
      "Too many pair attempts in the last minute. Wait 60 seconds, restart " +
      "the daemon with `coral stop && coral up`, then try again.",
    missing_authorization: "Not paired yet. Paste the challenge below and click Pair.",
    invalid_authorization_scheme: "Token mangled in transit. Click Unpair and re-pair.",
    invalid_token: "Token rejected by the daemon. Click Unpair and re-pair.",
    token_expired:
      "Token expired (and auto-refresh didn't fire). Click Unpair and re-pair.",
    session_not_found: "Session no longer exists in the vault.",
    session_not_active:
      "That session is revoked or expired and can't be refreshed. Capture a " +
      "new one instead.",
    active_session_exists_for_origin:
      "You already have an active session for this origin. Revoke it first, " +
      "or just use it.",
    audit_log_write_failed:
      "Coral couldn't write to its audit log — likely a corrupted vault or " +
      "disk full. Check `coral diagnose` in your terminal.",
    not_paired: "Pair the extension first by pasting the handshake challenge.",
    "session_handle_not_found": "That browser session is no longer open.",
  };
  return map[raw] ?? raw;
}

function renderSessions(sessions: SessionListItem[]): void {
  const list = $<HTMLUListElement>("sessions-list");
  list.innerHTML = "";
  const empty = $<HTMLParagraphElement>("empty-sessions");
  empty.hidden = sessions.length > 0;
  for (const s of sessions) {
    const li = document.createElement("li");
    const originSpan = document.createElement("span");
    originSpan.className = "origin";
    originSpan.textContent = s.origin;

    const statusSpan = document.createElement("span");
    statusSpan.className = `status ${s.status}`;
    statusSpan.textContent = s.status;

    const refreshBtn = document.createElement("button");
    refreshBtn.textContent = "Refresh";
    refreshBtn.className = "refresh";
    refreshBtn.title =
      "Re-capture this session from your current tab without losing the " +
      "session id. Navigate to " +
      s.origin +
      " first.";
    refreshBtn.disabled = s.status !== "active";
    refreshBtn.addEventListener("click", async () => {
      refreshBtn.disabled = true;
      const r = await rpc({
        type: "refresh_session",
        sessionId: s.id,
        origin: s.origin,
      });
      if (r.ok) {
        renderState(r.state);
      } else {
        setError(r.error);
        refreshBtn.disabled = false;
      }
    });

    const revokeBtn = document.createElement("button");
    revokeBtn.textContent = "Revoke";
    revokeBtn.disabled = s.status !== "active";
    revokeBtn.addEventListener("click", async () => {
      revokeBtn.disabled = true;
      const r = await rpc({ type: "revoke_session", sessionId: s.id });
      if (r.ok) {
        renderState(r.state);
      } else {
        setError(r.error);
      }
    });

    li.appendChild(originSpan);
    li.appendChild(statusSpan);
    li.appendChild(refreshBtn);
    li.appendChild(revokeBtn);
    list.appendChild(li);
  }
}

function renderState(state: AppState): void {
  setError(state.lastError);
  if (!state.daemonReachable) {
    $<HTMLParagraphElement>("status").textContent = "Daemon offline";
    show("offline-section", true);
    show("pair-section", false);
    show("paired-section", false);
    return;
  }
  show("offline-section", false);
  if (!state.paired) {
    $<HTMLParagraphElement>("status").textContent = "Daemon reachable — not yet paired";
    show("pair-section", true);
    show("paired-section", false);
    // Try to pre-fill the challenge from the clipboard.
    void autodetectClipboardChallenge();
    return;
  }

  show("pair-section", false);
  show("paired-section", true);
  const expiresAt = state.tokenExpiresAt;
  const minutes = expiresAt
    ? Math.max(0, Math.round((expiresAt - Math.floor(Date.now() / 1000)) / 60))
    : 0;
  $<HTMLParagraphElement>("status").textContent =
    `Paired — token expires in ${minutes}m (auto-refresh on)`;

  const captureBtn = $<HTMLButtonElement>("capture-btn");
  if (state.currentOrigin) {
    $<HTMLElement>("origin-text").textContent = state.currentOrigin;
    captureBtn.disabled = false;
    captureBtn.dataset.origin = state.currentOrigin;
  } else {
    $<HTMLElement>("origin-text").textContent = "(not a capturable page)";
    captureBtn.disabled = true;
    delete captureBtn.dataset.origin;
  }

  renderSessions(state.sessions);
}

/**
 * If the clipboard contains a Coral handshake challenge, pre-fill the input
 * and surface a hint so the user knows it's auto-detected. Silently no-ops
 * when the clipboard contains anything else.
 *
 * Requires the ``clipboardRead`` permission (declared in manifest.json).
 */
async function autodetectClipboardChallenge(): Promise<void> {
  const input = $<HTMLInputElement>("challenge-input");
  if (input.value.trim()) return; // user already typed; don't clobber
  let clipboardText = "";
  try {
    clipboardText = (await navigator.clipboard.readText()).trim();
  } catch {
    // Permission denied / not focused / unsupported — silently skip.
    return;
  }
  if (!CHALLENGE_RE.test(clipboardText)) return;
  input.value = clipboardText;
  const hint = $<HTMLParagraphElement>("clipboard-hint");
  hint.hidden = false;
  hint.textContent = "Detected challenge from clipboard. Click Pair to continue.";
  // Move focus to the pair button so Enter / Space pairs immediately.
  $<HTMLButtonElement>("pair-btn").focus();
}

async function refresh(): Promise<void> {
  const r = await rpc({ type: "get_state" });
  if (r.ok) {
    renderState(r.state);
  } else {
    setError(r.error);
  }
}

function bindHandlers(): void {
  $("retry-btn").addEventListener("click", () => void refresh());

  $("pair-btn").addEventListener("click", async () => {
    const input = $<HTMLInputElement>("challenge-input");
    const challenge = input.value.trim();
    if (!challenge) {
      setError("Paste the challenge first.");
      return;
    }
    const r = await rpc({ type: "pair", challenge });
    if (r.ok) {
      input.value = "";
      const hint = $<HTMLParagraphElement>("clipboard-hint");
      hint.hidden = true;
      renderState(r.state);
    } else {
      setError(r.error);
    }
  });

  $("capture-btn").addEventListener("click", async () => {
    const btn = $<HTMLButtonElement>("capture-btn");
    const origin = btn.dataset.origin;
    if (!origin) return;
    btn.disabled = true;
    const r = await rpc({ type: "capture", origin });
    if (r.ok) {
      renderState(r.state);
    } else {
      setError(r.error);
      btn.disabled = false;
    }
  });

  $("unpair-btn").addEventListener("click", async () => {
    const r = await rpc({ type: "unpair" });
    if (r.ok) renderState(r.state);
    else setError(r.error);
  });
}

bindHandlers();
void refresh();
