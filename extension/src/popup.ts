/// <reference types="chrome" />

import "./popup.css";

import type { AppState, PopupRequest, PopupResponse, SessionListItem } from "./messages.js";

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
    $<HTMLParagraphElement>("error-text").textContent = msg;
  }
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
