import { afterEach, describe, expect, it, vi } from "vitest";

import { DaemonClient, DaemonError } from "../client.js";

afterEach(() => {
  vi.restoreAllMocks();
});

function mockResponse(body: unknown, init: Partial<{ status: number; ok: boolean }> = {}) {
  const status = init.status ?? 200;
  const ok = init.ok ?? (status >= 200 && status < 300);
  return {
    ok,
    status,
    text: async () => (typeof body === "string" ? body : JSON.stringify(body)),
  } as Response;
}

describe("DaemonClient", () => {
  it("posts handshake without a bearer token", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      mockResponse({ token: "tok", expires_at: 9999 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const c = new DaemonClient("http://127.0.0.1:8765");
    const r = await c.handshake("ABCD-EFGH-JKLM-NPQR", "extension");

    expect(r).toEqual({ token: "tok", expires_at: 9999 });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8765/auth/handshake");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      challenge: "ABCD-EFGH-JKLM-NPQR",
      client_name: "extension",
    });
    expect(init.headers).not.toHaveProperty("Authorization");
  });

  it("attaches bearer token for authenticated calls", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(mockResponse({ sessions: [] }));
    vi.stubGlobal("fetch", fetchMock);

    const c = new DaemonClient();
    await c.listSessions("secret");

    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers).toMatchObject({ Authorization: "Bearer secret" });
  });

  it("throws DaemonError on 4xx with parsed error body", async () => {
    // mock returns a fresh response each call; the test makes one assertion-call.
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        mockResponse({ error: "invalid_token" }, { status: 401, ok: false }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const c = new DaemonClient();
    const err = await c.listSessions("bad").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(DaemonError);
    expect((err as DaemonError).status).toBe(401);
    expect((err as DaemonError).message).toBe("invalid_token");
  });

  it("revokeSession DELETEs and tolerates 204", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(mockResponse("", { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    const c = new DaemonClient();
    await c.revokeSession("tok", "session-uuid-123");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8765/sessions/session-uuid-123");
    expect(init.method).toBe("DELETE");
  });

  it("refreshSession PUTs to /sessions/{id}/refresh with state body", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      mockResponse({ session_id: "session-uuid-123", status: "active", expires_at: 9999 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const c = new DaemonClient();
    const stateBlob = {
      version: 1 as const,
      captured_at: 100,
      user_agent: "test",
      origin: "https://example.com",
      cookies: [],
      local_storage: {},
      session_storage: {},
    };
    const r = await c.refreshSession("tok", "session-uuid-123", {
      origin: "https://example.com",
      state: stateBlob,
    });

    expect(r.session_id).toBe("session-uuid-123");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8765/sessions/session-uuid-123/refresh");
    expect(init.method).toBe("PUT");
    expect(init.headers).toMatchObject({ Authorization: "Bearer tok" });
    expect(JSON.parse(init.body)).toEqual({
      origin: "https://example.com",
      state: stateBlob,
    });
  });

  it("attention fields round-trip through listSessions", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      mockResponse({
        sessions: [
          {
            id: "s1",
            origin: "https://example.com",
            label: null,
            created_at: 1,
            last_used_at: null,
            expires_at: null,
            status: "active",
            attention_at: 12345,
            attention_reason: "http_401",
          },
        ],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const c = new DaemonClient();
    const { sessions } = await c.listSessions("tok");
    expect(sessions[0].attention_reason).toBe("http_401");
    expect(sessions[0].attention_at).toBe(12345);
  });

  it("refreshSession URL-encodes the session id", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      mockResponse({ session_id: "weird/id", status: "active", expires_at: null }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const c = new DaemonClient();
    await c.refreshSession("tok", "weird/id", {
      origin: "https://example.com",
      state: {
        version: 1,
        captured_at: 1,
        user_agent: "x",
        origin: "https://example.com",
        cookies: [],
        local_storage: {},
        session_storage: {},
      },
    });

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8765/sessions/weird%2Fid/refresh");
  });
});
