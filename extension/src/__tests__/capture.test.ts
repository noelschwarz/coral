/// <reference types="chrome" />

import { describe, expect, it } from "vitest";

import type { ChromeCookieLike } from "../capture.js";
import { normalizeCookie, originOfUrl } from "../capture.js";

describe("originOfUrl", () => {
  it("returns scheme + host with no path", () => {
    expect(originOfUrl("https://example.com/feed/me?x=1")).toBe("https://example.com");
    expect(originOfUrl("http://localhost:3000/x")).toBe("http://localhost:3000");
  });

  it("rejects non-http(s) schemes", () => {
    expect(() => originOfUrl("chrome://extensions")).toThrow(/unsupported scheme/);
    expect(() => originOfUrl("file:///tmp/x")).toThrow(/unsupported scheme/);
  });
});

describe("normalizeCookie", () => {
  const base: ChromeCookieLike = {
    name: "sid",
    value: "abc",
    domain: ".example.com",
    path: "/",
    secure: true,
    httpOnly: true,
    hostOnly: false,
    session: false,
    sameSite: "lax",
    storeId: "0",
  };

  it("maps the §4.2 shape with camelCase same-site", () => {
    const c = normalizeCookie({ ...base, expirationDate: 1730000000.5 });
    expect(c).toEqual({
      name: "sid",
      value: "abc",
      domain: ".example.com",
      path: "/",
      expires: 1730000000.5,
      httpOnly: true,
      secure: true,
      sameSite: "Lax",
    });
  });

  it("omits expires for session cookies", () => {
    const c = normalizeCookie({ ...base, session: true });
    expect(c.expires).toBeUndefined();
  });

  it("normalizes each sameSite variant", () => {
    expect(normalizeCookie({ ...base, sameSite: "strict" }).sameSite).toBe("Strict");
    expect(normalizeCookie({ ...base, sameSite: "lax" }).sameSite).toBe("Lax");
    expect(normalizeCookie({ ...base, sameSite: "no_restriction" }).sameSite).toBe("None");
    expect(normalizeCookie({ ...base, sameSite: "unspecified" }).sameSite).toBeUndefined();
  });
});
