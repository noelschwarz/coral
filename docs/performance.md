# Performance baseline

Measured against `main` at v0.5.0 (post-Track G) on a 2024 M2 MacBook Pro with
Python 3.11.15. **These numbers are not gates** — spec §8.4 explicitly
de-emphasizes performance benchmarks for v1. They exist so regressions are
visible.

Re-measure with `tests/manual/perf_baseline.py` (script lives in `tests/manual/`
and is not part of CI).

## Daemon startup

| Operation | Time | Notes |
|---|---|---|
| `coral init` (fresh vault) | ~600 ms | Dominated by Argon2id key derivation with `PRODUCTION_PARAMS` (m=64MB, t=3, p=4 — spec §6.3). |
| `coral start` (unlock existing) | ~550 ms | Same Argon2id cost. |
| Orphan-process sweep | < 50 ms | Scans `psutil.process_iter`; short-circuits on non-Chromium names. |
| Behavior-pack seed (six packs, all already present) | < 20 ms | Six `SELECT` per origin; no inserts after first run. |

## Session lifecycle (Playwright)

| Operation | Time | Notes |
|---|---|---|
| `coral_open_session` (Chromium cold start) | ~700 ms | `launch_persistent_context` + `add_cookies` + init script registration + read `/json/version`. |
| `coral_open_session` (subsequent in same daemon) | ~650 ms | No warm pool yet — each open spawns a new browser per ADR-010. |
| `coral_close_session` | ~120 ms | `context.close()` + `rmtree(user_data_dir)`. |
| Idle daemon RAM (no open sessions) | ~80 MB | FastAPI + Uvicorn + Pydantic + Playwright Python wrapper. |
| Per open session RAM | ~150-200 MB | Each Chromium child process. |

## Vault throughput

| Operation | Throughput | Notes |
|---|---|---|
| Audit-row insert (single connection, single thread) | ~5,000/s | Limited by SQLite commit cost + writer-task scheduling. |
| Session insert (with ~5 KB compressed `state_blob`) | ~1,500/s | Dominated by gzip + SQLCipher page encryption. |
| `list_sessions` (100 rows) | < 5 ms | Single-row decode is the bottleneck, not SQL. |
| `query_audit(since=None, limit=100)` | < 10 ms | Indexed on `timestamp`. |

The integration test `test_concurrent_writes_serialized` fires 100 audit inserts
via `asyncio.gather` and asserts all 100 land; runs in ~250 ms on the reference
machine, implying the single writer task handles ~400 writes/s under contention
(lower than the bulk number above due to coroutine scheduling overhead).

## Test suite

| Suite | Tests | Time | Notes |
|---|---|---|---|
| `tests/unit` | 118 | ~6 s | No Playwright, no subprocess. |
| `tests/integration` | 11 | ~12 s | Real Chromium for SessionServer + MCP-stdio + policy enforcement + panic. |
| `tests/e2e` | 1 | ~3 s | Capture → MCP → CDP → drive → close. |
| **Total** | **130+** | **~25 s** | Comfortably under the spec §8 60-second envelope. |

## Hotspots worth optimizing (post-v1)

1. **Per-session Chromium memory.** ADR-010 chose isolation over efficiency.
   When ≥5 concurrent sessions become common, the shared-Chromium-with-
   CDP-target-filter design needs to ship (ADR-012 deferral #2).
2. **Argon2id ~500 ms cost per daemon start.** Conscious tradeoff for offline
   passphrase brute-force resistance (spec §6.3 / T9). Could be amortized via
   an OS-keychain unwrap (an ADR-009 follow-up I explicitly recommended) if
   we ever move off the OS-agnostic posture.
3. **Vault writer task is async-loop-bound.** ADR-012 deferral #1. Doesn't
   affect throughput today; will block any future subprocess that needs vault
   access.

## How to re-measure

```bash
uv run python -m tests.manual.perf_baseline
# Prints a table of timings; appends to docs/performance.md history.
```

History lives in this file. Add a row dated when the numbers change
materially (e.g. when Argon2id parameters change, or when a session-pool
optimization ships).

## Changelog

- **2026-05 (v0.5.0, Track G)** — baseline established. Numbers above.
