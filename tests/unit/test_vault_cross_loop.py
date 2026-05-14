"""Cross-loop tests for the vault worker-thread architecture (Track J', ADR-015).

These tests would have deadlocked under the pre-Track-J' design (vault
writer task bound to a specific event loop). With the worker-thread
architecture they pass reliably.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from coral.crypto import TEST_PARAMS
from coral.vault import Vault, make_demo_session_record, unlock_vault


def _run_in_fresh_loop(coro_factory):
    """Spin up a brand-new asyncio.run() each call.

    Each call gets its own loop. If anything in the vault is loop-bound
    in a way that survives across calls, this pattern surfaces it.
    """
    return asyncio.run(coro_factory())


def test_vault_survives_loop_recreation(tmp_path: Path) -> None:
    """Open on loop A, exit loop A, open `unlock_vault` on loop B, use it on
    a third loop. Each call uses its own fresh ``asyncio.run`` — the worker
    thread underneath them is the same."""
    passphrase = "correct horse battery staple"

    async def init() -> str:
        vault = await Vault.initialize(tmp_path, passphrase, params=TEST_PARAMS)
        rec = make_demo_session_record()
        await vault.insert_session(rec)
        await vault.close()
        return rec.id

    session_id = _run_in_fresh_loop(lambda: init())

    async def reopen_and_read() -> int:
        vault = await unlock_vault(home=tmp_path, passphrase=passphrase)
        try:
            got = await vault.get_session(session_id)
            assert got is not None
            return 1
        finally:
            await vault.close()

    assert _run_in_fresh_loop(lambda: reopen_and_read()) == 1


def test_vault_works_with_threadpool_run_concurrently(tmp_path: Path) -> None:
    """Open the vault once on loop A; from a *separate thread* with its own
    asyncio.run, issue a write. Must not deadlock."""
    passphrase = "correct horse battery staple"

    async def setup() -> Vault:
        return await Vault.initialize(tmp_path, passphrase, params=TEST_PARAMS)

    vault: Vault = asyncio.run(setup())

    result: dict[str, object] = {}

    def worker() -> None:
        async def do_write() -> None:
            rec = make_demo_session_record(origin="https://thread.example")
            await vault.insert_session(rec)
            got = await vault.get_session(rec.id)
            assert got is not None
            result["origin"] = got.origin

        asyncio.run(do_write())

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=10.0)
    assert not t.is_alive(), "worker thread deadlocked"
    assert result.get("origin") == "https://thread.example"

    asyncio.run(vault.close())


@pytest.mark.asyncio
async def test_many_concurrent_writes_from_one_loop(tmp_path: Path) -> None:
    """The original Track A concurrency test still holds with the new
    worker-thread design — 100 concurrent inserts via ``asyncio.gather``
    serialize cleanly and all land."""
    from coral.models import AuditEntry

    vault = await Vault.initialize(tmp_path, "correct horse battery staple", params=TEST_PARAMS)

    async def insert_one(i: int) -> None:
        await vault.insert_audit(
            AuditEntry(
                timestamp=1_700_000_000 + i,
                session_id=None,
                agent_id=None,
                event_type="test.write",
                origin=None,
                detail=f'{{"i":{i}}}',
            )
        )

    await asyncio.gather(*(insert_one(i) for i in range(100)))
    rows = await vault.query_audit(since=None, limit=200)
    assert len([r for r in rows if r.event_type == "test.write"]) == 100
    await vault.close()


@pytest.mark.asyncio
async def test_worker_stops_cleanly_on_close(tmp_path: Path) -> None:
    """``Vault.close()`` joins the worker thread; verify it actually
    terminates (not just orphans the thread)."""
    vault = await Vault.initialize(tmp_path, "correct horse battery staple", params=TEST_PARAMS)
    assert vault._worker_thread is not None
    worker_ref = vault._worker_thread
    assert worker_ref.is_alive()
    await vault.close()
    assert not worker_ref.is_alive()
    assert vault._worker_thread is None
