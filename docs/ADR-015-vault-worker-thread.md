# ADR-015: Vault worker-thread architecture

## Status

Accepted — Track J' (2026). Resolves ADR-012 deferral #1.

## Context

The original ``coral/vault.py`` used three loop-bound primitives:

1. ``asyncio.Queue[_WriteCmd | object]`` — the write queue, created in the
   loop that called ``Vault.initialize`` / ``Vault.open``.
2. ``asyncio.Task`` writer — pulls items from the queue in that same loop.
3. ``asyncio.Future`` per write — created via ``loop.create_future()`` in
   the calling loop.

Reads went through a separate ``ThreadPoolExecutor(max_workers=1)`` via
``loop.run_in_executor`` — that part was loop-agnostic because each call
created its own per-call future on the calling loop.

The architecture worked when the daemon ran on a single long-lived loop.
It failed any time a second loop entered the picture:

- **Test bite #1** (Track B): the original ``test_auth.py`` used
  Starlette's sync ``TestClient``. ``TestClient`` runs requests on a
  ``anyio.start_blocking_portal`` loop, *not* the pytest-asyncio loop where
  the vault was opened. The middleware's ``await vault.verify_token(...)``
  ended up calling ``vault._enqueue_write`` (well, ``touch_token_last_used``)
  which created a future on the portal loop and put it on a queue whose
  awaiter (the writer task) lived on the dead pytest-asyncio loop. Hang.
  **Fix at the time:** rewrite the auth tests to use ``httpx.AsyncClient``
  so request handling shared the test loop.
- **Test bite #2** (Track D): the MCP HTTP success-path ``TestClient`` test
  hit the same wall and was deferred. ADR-012 documented the deferral.
- **Future bite:** any subprocess that wants to share a vault — a tray
  app, a notification listener, a webhook receiver — would deadlock the
  same way the moment it creates a second loop.

The deferral was meant to be temporary. Track J' resolves it.

## Decision

**Replace the loop-bound primitives with thread-bound primitives.**

| Old | New |
|---|---|
| ``asyncio.Queue[_WriteCmd]`` | ``queue.Queue[_WorkItem]`` |
| ``asyncio.Task`` writer (on the daemon loop) | ``threading.Thread`` worker (OS thread) |
| ``asyncio.Future`` per call | ``concurrent.futures.Future`` per call, wrapped via ``asyncio.wrap_future`` at the call site |
| Separate ``ThreadPoolExecutor`` for reads | Same worker queue + thread handles both reads and writes |

Concretely:

```python
class Vault:
    def __init__(self, db_path: Path) -> None:
        self._cmd_queue: queue.Queue[_WorkItem | object] = queue.Queue()
        self._worker_thread: threading.Thread | None = None

    async def _run_sync(self, fn: Callable[[], T]) -> T:
        fut: concurrent.futures.Future[T] = concurrent.futures.Future()
        self._cmd_queue.put(_WorkItem(fn=fn, fut=fut))
        return await asyncio.wrap_future(fut)

    def _start_worker(self) -> None:
        def runner() -> None:
            while True:
                item = self._cmd_queue.get()
                if item is _WORKER_STOP:
                    return
                try:
                    item.fut.set_result(item.fn())
                except BaseException as exc:
                    if not item.fut.done():
                        item.fut.set_exception(exc)
        self._worker_thread = threading.Thread(
            target=runner, name="coral-vault-worker", daemon=True,
        )
        self._worker_thread.start()
```

Reads and writes both call ``_run_sync`` — there's no longer a separate
write queue. SQLite serializes commits anyway, and routing everything
through one queue keeps the "exactly one thread touches the connection"
invariant trivially true.

## Consequences

- **The same ``Vault`` is now safe to share across asyncio loops.**
  ``Vault.initialize`` on one ``asyncio.run`` call, then awaiting
  ``vault.insert_session(...)`` from another, no longer deadlocks.
- **The previously-deferred MCP HTTP success-path test is back on.**
  ``tests/unit/test_mcp_server.py::test_authed_mcp_http_accepts_valid_token``
  exercises Starlette's sync ``TestClient`` against the FastMCP HTTP app,
  which spins up the lifespan on the portal loop and then re-uses the
  same vault from a different loop. Pre-Track-J' this hung; now it runs
  in 0.7 s.
- **No public API change.** Every ``Vault`` method has the same signature
  and the same semantics. 163 existing tests pass with the rewrite, plus
  4 new cross-loop tests demonstrating the previously-broken patterns
  now succeed (``tests/unit/test_vault_cross_loop.py``).
- **Throughput is unchanged.** The single-thread executor used to handle
  reads-and-writes-via-its-thread-pool; the new worker thread does the
  same dispatch via a single Python ``queue.Queue``. Same number of
  threads, same serialization properties.

## Accepted limitations

- **The worker thread is a daemon thread.** If the main process dies
  ungracefully (SIGKILL, segfault), the worker dies with it and any
  in-flight write is lost — same as before. The 10-second ``join``
  timeout in ``Vault.close()`` is a defense against a stuck worker, not
  a guarantee of clean shutdown.
- **No back-pressure on the queue.** ``queue.Queue`` is unbounded; if a
  caller floods writes faster than SQLCipher can commit them, the queue
  grows without limit. The previous ``asyncio.Queue`` was also unbounded
  so this isn't a regression, but it's worth flagging in case future
  high-throughput agents push it. A future ``maxsize`` could backpressure
  callers via ``queue.Queue.put_nowait`` → ``queue.Full`` if needed.
- **No per-call cancellation propagation from caller-side cancel to
  worker-side abort.** If the caller's coroutine is cancelled while
  awaiting ``asyncio.wrap_future(fut)``, the underlying SQL statement
  still runs on the worker; only the result is dropped on the caller's
  side. SQLite doesn't expose a cancel-statement primitive that would let
  us do better, and audit/lifecycle writes shouldn't be cancellable
  anyway. Not a regression.

## When to revisit

- If the queue grows unbounded under real workloads, add a ``maxsize`` +
  backpressure policy.
- If we add a second worker (read-only sibling, perhaps a replica
  connection), the single-queue invariant changes — write a new ADR then.
- If Python ever ships a thread-safe ``asyncio.Queue`` variant that
  works across loops, we can collapse back to the old design. Unlikely.

## Resolves

- ADR-012 deferral #1 (vault writer cross-event-loop decoupling).
- ADR-012 deferral #5 (MCP HTTP success-path test "intentionally absent"
  comment in ``tests/unit/test_mcp_server.py``). The test is now active.
