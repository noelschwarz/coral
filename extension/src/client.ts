/**
 * HTTP client for the Coral daemon (``127.0.0.1:8765``).
 *
 * The daemon refuses to bind anything other than loopback (spec §6.2 T2), so
 * every request here is to ``127.0.0.1`` by construction. Bearer tokens are
 * passed by callers and never logged.
 */

import type { SessionListItem } from "./messages.js";
import { DEFAULT_DAEMON_BASE } from "./messages.js";

export type HandshakeResponse = {
  token: string;
  expires_at: number;
};

export type RefreshResponse = {
  token: string;
  expires_at: number;
  previous_revoked: boolean;
};

export type CaptureResponse = {
  session_id: string;
  status: "active" | "expired" | "revoked";
  expires_at: number | null;
};

export type StateBlob = {
  version: 1;
  captured_at: number;
  user_agent: string;
  origin: string;
  cookies: CapturedCookie[];
  local_storage: Record<string, string>;
  session_storage: Record<string, string>;
};

export type CapturedCookie = {
  name: string;
  value: string;
  domain: string;
  path: string;
  expires?: number;
  httpOnly?: boolean;
  secure?: boolean;
  sameSite?: "Strict" | "Lax" | "None";
};

export class DaemonError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body: unknown,
  ) {
    super(message);
  }
}

export class DaemonClient {
  constructor(private readonly base: string = DEFAULT_DAEMON_BASE) {}

  async healthz(): Promise<{ status: string; version: string }> {
    const r = await fetch(`${this.base}/healthz`);
    if (!r.ok) throw new DaemonError("healthz failed", r.status, null);
    return r.json();
  }

  async handshake(challenge: string, clientName: string): Promise<HandshakeResponse> {
    return this.json<HandshakeResponse>("POST", "/auth/handshake", null, {
      challenge,
      client_name: clientName,
    });
  }

  async refresh(token: string): Promise<RefreshResponse> {
    return this.json<RefreshResponse>("POST", "/auth/refresh", token);
  }

  async captureSession(
    token: string,
    body: { origin: string; label?: string; state: StateBlob },
  ): Promise<CaptureResponse> {
    return this.json<CaptureResponse>("POST", "/sessions", token, body);
  }

  /**
   * Re-capture an existing session in place (PR N2).
   *
   * Preserves the session_id so open agent handles aren't invalidated. The
   * daemon rejects (409) if the session is already revoked/expired, and (400)
   * if the body's origin doesn't match the captured session's origin.
   */
  async refreshSession(
    token: string,
    sessionId: string,
    body: { origin: string; label?: string; state: StateBlob },
  ): Promise<CaptureResponse> {
    return this.json<CaptureResponse>(
      "PUT",
      `/sessions/${encodeURIComponent(sessionId)}/refresh`,
      token,
      body,
    );
  }

  async listSessions(token: string): Promise<{ sessions: SessionListItem[] }> {
    return this.json<{ sessions: SessionListItem[] }>("GET", "/sessions", token);
  }

  async revokeSession(token: string, sessionId: string): Promise<void> {
    await this.json<unknown>(
      "DELETE",
      `/sessions/${encodeURIComponent(sessionId)}`,
      token,
    );
  }

  private async json<T>(
    method: string,
    path: string,
    token: string | null,
    body?: unknown,
  ): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (token) headers.Authorization = `Bearer ${token}`;
    if (body !== undefined) headers["Content-Type"] = "application/json";

    const res = await fetch(`${this.base}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });

    // 204 No Content
    if (res.status === 204) return undefined as T;

    const text = await res.text();
    const parsed: unknown = text ? safeJsonParse(text) : null;
    if (!res.ok) {
      const msg = errorMessage(parsed) ?? `${method} ${path} → ${res.status}`;
      throw new DaemonError(msg, res.status, parsed);
    }
    return parsed as T;
  }
}

function safeJsonParse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function errorMessage(parsed: unknown): string | null {
  if (parsed && typeof parsed === "object" && "error" in parsed) {
    const e = (parsed as { error?: unknown }).error;
    if (typeof e === "string") return e;
  }
  return null;
}
