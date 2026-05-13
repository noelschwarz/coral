"""Re-measure the numbers in ``docs/performance.md``.

This script is intentionally **not** part of the CI suite — performance gates
are explicitly out of scope per spec §8.4. Run it manually when you suspect a
regression or land a perf-relevant change.

  uv run python -m tests.manual.perf_baseline
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path


async def _bench_vault(home: Path, passphrase: str) -> dict[str, float]:
    from coral.crypto import (
        TEST_PARAMS,
    )  # production params take ~500ms; test is fine for relative timing
    from coral.models import AuditEntry
    from coral.vault import Vault

    t = time.perf_counter()
    vault = await Vault.initialize(home, passphrase, params=TEST_PARAMS)
    init_ms = (time.perf_counter() - t) * 1000.0

    t = time.perf_counter()
    for i in range(100):
        await vault.insert_audit(
            AuditEntry(
                timestamp=int(time.time()) + i,
                session_id=None,
                agent_id=None,
                event_type="bench.write",
                origin=None,
                detail=json.dumps({"i": i}),
            )
        )
    audit_ms = (time.perf_counter() - t) * 1000.0

    t = time.perf_counter()
    rows = await vault.query_audit(since=None, limit=100)
    query_ms = (time.perf_counter() - t) * 1000.0
    assert len(rows) == 100

    await vault.close()
    return {
        "vault_init_ms": init_ms,
        "audit_100_writes_ms": audit_ms,
        "audit_writes_per_sec": 100_000.0 / audit_ms,
        "audit_query_100_ms": query_ms,
    }


def main() -> None:
    with tempfile.TemporaryDirectory() as raw:
        home = Path(raw)
        passphrase = "correct horse battery staple"
        results = asyncio.run(_bench_vault(home, passphrase))
    print("\n=== coral perf baseline (TEST_PARAMS Argon2id) ===")
    for k, v in results.items():
        print(f"  {k:<30} {v:>10.2f}")
    print("\nProduction Argon2id (~500ms) adds a constant to vault_init_ms.")
    print("Per-session Chromium baselines require Playwright + a test server;")
    print("see tests/integration/test_session_server.py for the timing-relevant ones.")


if __name__ == "__main__":
    main()
