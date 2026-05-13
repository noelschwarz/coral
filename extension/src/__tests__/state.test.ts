import { describe, expect, it } from "vitest";

import {
  emptyState,
  isTokenValid,
  loadState,
  REFRESH_THRESHOLD_SECONDS,
  saveState,
  shouldRefresh,
  type PersistedState,
  type StorageArea,
} from "../state.js";

function makeMemoryArea(): StorageArea & { dump(): Record<string, unknown> } {
  let mem: Record<string, unknown> = {};
  return {
    async get(key: string) {
      return key in mem ? { [key]: mem[key] } : {};
    },
    async set(items: Record<string, unknown>) {
      mem = { ...mem, ...items };
    },
    dump() {
      return mem;
    },
  };
}

describe("loadState / saveState", () => {
  it("returns empty state when storage is empty", async () => {
    const area = makeMemoryArea();
    const s = await loadState(area);
    expect(s).toEqual(emptyState());
  });

  it("round-trips a populated state", async () => {
    const area = makeMemoryArea();
    const original: PersistedState = {
      token: "abc",
      tokenExpiresAt: 9999999999,
      daemonBase: "http://127.0.0.1:1234",
    };
    await saveState(area, original);
    expect(await loadState(area)).toEqual(original);
  });

  it("ignores garbage in storage", async () => {
    const area = makeMemoryArea();
    await area.set({ "coral.persisted": 42 });
    expect(await loadState(area)).toEqual(emptyState());
  });
});

describe("isTokenValid", () => {
  it("requires both a token and an unexpired timestamp", () => {
    const valid: PersistedState = {
      token: "x",
      tokenExpiresAt: 1000,
      daemonBase: "http://127.0.0.1:8765",
    };
    expect(isTokenValid(valid, 500)).toBe(true);
    expect(isTokenValid(valid, 1500)).toBe(false);
    expect(isTokenValid({ ...valid, token: null }, 500)).toBe(false);
    expect(isTokenValid({ ...valid, tokenExpiresAt: null }, 500)).toBe(false);
  });
});

describe("shouldRefresh", () => {
  const base: PersistedState = {
    token: "x",
    tokenExpiresAt: 10_000,
    daemonBase: "http://127.0.0.1:8765",
  };

  it("returns false when no token", () => {
    expect(shouldRefresh({ ...base, token: null }, 0)).toBe(false);
  });

  it("returns false when the token is expired", () => {
    expect(shouldRefresh(base, 20_000)).toBe(false);
  });

  it("returns false when there's plenty of time left", () => {
    expect(shouldRefresh(base, 10_000 - REFRESH_THRESHOLD_SECONDS - 1)).toBe(false);
  });

  it("returns true inside the refresh threshold", () => {
    expect(shouldRefresh(base, 10_000 - REFRESH_THRESHOLD_SECONDS + 1)).toBe(true);
  });
});
