/**
 * HTTP client for Coral daemon (`fetch` to 127.0.0.1:8765) — week 1.
 */

export const DAEMON_BASE = "http://127.0.0.1:8765";

export async function getHealthz(): Promise<unknown> {
  const r = await fetch(`${DAEMON_BASE}/healthz`);
  if (!r.ok) {
    throw new Error(`healthz failed: ${r.status}`);
  }
  return r.json();
}
