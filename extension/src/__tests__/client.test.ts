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
});
