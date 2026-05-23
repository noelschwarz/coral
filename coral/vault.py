"""Encrypted vault (SQLCipher) with a dedicated worker thread (ADR-015).

SQLite + SQLCipher connections are not thread-safe; they must only be touched
from the thread that opened them. We satisfy that by routing every
read and every write through a single OS thread via a ``queue.Queue`` of
``_WorkItem``s. Per-call ``concurrent.futures.Future`` instances bridge the
worker thread back to whatever asyncio loop the caller happens to be on —
which means the same ``Vault`` instance is safe to share across multiple
asyncio loops (the cross-loop deadlock that bit Tracks B and D is gone).

Do **not** replace this with a naive ``asyncio.Lock`` + executor: it
re-introduces the loop-bound primitive that caused the original bug.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import queue
import sqlite3
import threading
import time
import uuid
from base64 import b64decode, b64encode
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, NamedTuple, TypeVar, cast

import sqlcipher3.dbapi2 as sqlcipher

from coral.crypto import (
    PRODUCTION_PARAMS,
    Argon2idParams,
    derive_key,
    format_sqlcipher_hex_pragma_key,
    generate_salt,
    hash_token,
)
from coral.models import (
    AuditEntry,
    PolicyRecord,
    ReviewRecord,
    ReviewStatus,
    SessionRecord,
    SessionStatus,
    TokenRecord,
)

_WORKER_STOP: Final = object()

T = TypeVar("T")


class VaultError(RuntimeError):
    """Base vault failure."""


class VaultLockedError(VaultError):
    """Wrong passphrase or unreadable vault."""


class VaultMigrationError(VaultError):
    """Migration failure."""


class VaultIntegrityError(VaultError):
    """Encrypted metadata disagrees with plaintext derivation metadata."""


@dataclass(frozen=True, slots=True)
class PlaintextVaultMeta:
    """Salt + Argon2 parameters stored beside ``vault.db`` for derivation."""

    salt: bytes
    params: Argon2idParams

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "salt_b64": b64encode(self.salt).decode("ascii"),
            "memory_cost": self.params.memory_cost,
            "time_cost": self.params.time_cost,
            "parallelism": self.params.parallelism,
            "hash_len": self.params.hash_len,
            "salt_len": self.params.salt_len,
        }

    @classmethod
    def from_json_dict(cls, raw: dict[str, Any]) -> PlaintextVaultMeta:
        salt = b64decode(str(raw["salt_b64"]).encode("ascii"))
        params = Argon2idParams(
            memory_cost=int(raw["memory_cost"]),
            time_cost=int(raw["time_cost"]),
            parallelism=int(raw["parallelism"]),
            hash_len=int(raw["hash_len"]),
            salt_len=int(raw["salt_len"]),
        )
        return cls(salt=salt, params=params)


class _WorkItem(NamedTuple):
    """One unit of work for the vault's worker thread.

    ``fn`` runs synchronously in the worker thread (which owns the SQLCipher
    connection). The result or exception lands in ``fut``, a
    ``concurrent.futures.Future`` — loop-agnostic, so callers from any
    asyncio loop can wrap it via ``asyncio.wrap_future`` (ADR-015).
    """

    fn: Callable[[], Any]
    fut: concurrent.futures.Future[Any]


def _zero_bytearray(buf: bytearray) -> None:
    buf[:] = b"\x00" * len(buf)


def compress_blob(data: dict[str, Any]) -> bytes:
    import gzip

    return gzip.compress(json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def decompress_blob(blob: bytes) -> dict[str, Any]:
    import gzip

    if not blob:
        return {}
    try:
        decoded = json.loads(gzip.decompress(blob).decode("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VaultIntegrityError("state_blob is not a valid gzipped JSON payload.") from exc
    if not isinstance(decoded, dict):
        raise VaultIntegrityError("state_blob payload must decode to a JSON object.")
    return cast(dict[str, Any], decoded)


def read_plaintext_meta(*, home: Path) -> PlaintextVaultMeta:
    from coral.paths import vault_plaintext_meta_path

    path = vault_plaintext_meta_path(home)
    if not path.is_file():
        raise VaultError(f"Missing vault derivation metadata: {path}")
    try:
        raw_any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise VaultError(f"Invalid JSON in vault derivation metadata ({path}).") from exc
    if not isinstance(raw_any, dict):
        raise VaultError(f"Vault derivation metadata must be an object ({path}).")
    return PlaintextVaultMeta.from_json_dict(cast(dict[str, Any], raw_any))


def write_plaintext_meta(*, home: Path, meta: PlaintextVaultMeta) -> Path:
    from coral.paths import vault_plaintext_meta_path

    path = vault_plaintext_meta_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(meta.to_json_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _migration_files() -> list[tuple[int, Path]]:
    root = Path(__file__).resolve().parent / "migrations"
    found: list[tuple[int, Path]] = []
    for path in sorted(root.glob("*.sql")):
        prefix = path.name.split("_", maxsplit=1)[0]
        found.append((int(prefix), path))
    return sorted(found, key=lambda item: item[0])


def _sync_applied_versions(conn: sqlcipher.Connection) -> set[int]:
    try:
        cur = conn.execute("SELECT version FROM schema_migrations ORDER BY version ASC")
        rows = cur.fetchall()
        cur.close()
        return {int(r[0]) for r in rows}
    except (sqlite3.OperationalError, sqlcipher.OperationalError):
        return set()


class Vault:
    """Async façade over a synchronous SQLCipher connection.

    All connection access happens on a dedicated OS thread (the "worker"). The
    queue is :class:`queue.Queue` and the per-call futures are
    :class:`concurrent.futures.Future` — both loop-agnostic — so the vault is
    safe to share across multiple asyncio event loops (ADR-015). This
    decoupling was the cure for the cross-loop deadlock that bit Tracks B
    and D: opening the vault on one loop and awaiting a write from another
    loop now works correctly.

    Reads and writes both pass through the same queue. SQLite serializes
    commits anyway, so throughput is unchanged; correctness is significantly
    better.
    """

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._conn: sqlcipher.Connection | None = None
        self._cmd_queue: queue.Queue[_WorkItem | object] = queue.Queue()
        self._worker_thread: threading.Thread | None = None

    async def _run_sync(self, fn: Callable[[], T]) -> T:
        """Dispatch ``fn`` to the worker thread and await its result.

        Loop-agnostic: caller may be running on any asyncio loop. The
        :class:`concurrent.futures.Future` we create is wrapped with
        :func:`asyncio.wrap_future` against whichever loop is currently
        running, so the dispatch is per-call rather than per-vault.
        """
        if self._worker_thread is None:
            raise VaultError("Vault worker thread is not running.")
        fut: concurrent.futures.Future[T] = concurrent.futures.Future()
        self._cmd_queue.put(_WorkItem(fn=fn, fut=fut))
        return await asyncio.wrap_future(fut)

    def _start_worker(self) -> None:
        """Spin up the worker thread. Called by ``open``/``initialize``."""
        if self._worker_thread is not None:
            raise VaultError("Vault worker thread already running.")

        def runner() -> None:
            while True:
                item = self._cmd_queue.get()
                if item is _WORKER_STOP:
                    return
                assert isinstance(item, _WorkItem)
                try:
                    result = item.fn()
                    item.fut.set_result(result)
                except BaseException as exc:  # noqa: BLE001 — re-raised via the future
                    if not item.fut.done():
                        item.fut.set_exception(exc)

        self._worker_thread = threading.Thread(
            target=runner,
            name="coral-vault-worker",
            daemon=True,
        )
        self._worker_thread.start()

    async def _stop_worker(self) -> None:
        """Drain pending work, signal stop, and join the thread."""
        if self._worker_thread is None:
            return
        self._cmd_queue.put(_WORKER_STOP)
        await asyncio.to_thread(self._worker_thread.join, 10.0)
        if self._worker_thread.is_alive():
            # Unexpected: a 10-second join should be more than enough for the
            # worker to process any in-flight item plus the stop sentinel.
            raise VaultError("Vault worker thread did not stop in 10s.")
        self._worker_thread = None

    @classmethod
    async def open(cls, path: Path, key: bytearray, *, plaintext_meta: PlaintextVaultMeta) -> Vault:
        """Unlock an existing vault and verify encrypted metadata."""
        self = cls(path)
        self._start_worker()
        try:
            await self._connect(key)
            await self._apply_pending_migrations()
            await self._verify_encrypted_meta(plaintext_meta)
        except VaultError:
            await self._dispose_connection_only()
            raise
        _zero_bytearray(key)
        return self

    @classmethod
    async def initialize(
        cls,
        home: Path,
        passphrase: str,
        *,
        params: Argon2idParams = PRODUCTION_PARAMS,
    ) -> Vault:
        """Create ``vault.db``, plaintext meta, and encrypted metadata row."""
        from coral.paths import vault_db_path

        db_path = vault_db_path(home)
        if db_path.exists():
            raise VaultError(f"Vault database already exists at {db_path}")

        meta = PlaintextVaultMeta(salt=generate_salt(params=params), params=params)
        write_plaintext_meta(home=home, meta=meta)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        key = derive_key(passphrase, meta.salt, params=params)
        self = cls(db_path)
        self._start_worker()
        try:
            await self._connect(key)
            await self._apply_pending_migrations()
            await self._upsert_crypto_meta(meta)
            await self._verify_encrypted_meta(meta)
        except VaultError:
            await self._dispose_connection_only()
            raise
        _zero_bytearray(key)
        return self

    async def close(self) -> None:
        if self._conn is not None:
            conn = self._conn
            self._conn = None

            def close_conn() -> None:
                conn.close()

            await self._run_sync(close_conn)
        await self._stop_worker()

    async def insert_session(self, session: SessionRecord) -> None:
        sql = """
            INSERT INTO sessions (
                id, origin, label, created_at, last_used_at, expires_at,
                status, state_blob, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        args = (
            session.id,
            session.origin,
            session.label,
            session.created_at,
            session.last_used_at,
            session.expires_at,
            session.status,
            session.state_blob,
            session.metadata,
        )
        await self._enqueue_write(sql, args)

    async def get_session(self, session_id: str) -> SessionRecord | None:
        conn = self._require_conn()

        def work() -> sqlite3.Row | tuple[Any, ...] | None:
            cur = conn.execute(
                """
                SELECT id, origin, label, created_at, last_used_at, expires_at,
                       status, state_blob, metadata
                FROM sessions WHERE id = ?
                """,
                (session_id,),
            )
            row = cur.fetchone()
            cur.close()
            return row

        row = await self._run_sync(work)
        if row is None:
            return None
        return SessionRecord(
            id=str(row[0]),
            origin=str(row[1]),
            label=cast(str | None, row[2]),
            created_at=int(row[3]),
            last_used_at=cast(int | None, row[4]),
            expires_at=cast(int | None, row[5]),
            status=cast(SessionStatus, str(row[6])),
            state_blob=bytes(row[7]),
            metadata=str(row[8]),
        )

    async def list_sessions(self) -> list[SessionRecord]:
        conn = self._require_conn()

        def work() -> list[sqlite3.Row | tuple[Any, ...]]:
            cur = conn.execute(
                """
                SELECT id, origin, label, created_at, last_used_at, expires_at,
                       status, state_blob, metadata
                FROM sessions ORDER BY created_at DESC
                """,
            )
            rows = cur.fetchall()
            cur.close()
            return list(rows)

        rows = await self._run_sync(work)
        out: list[SessionRecord] = []
        for row in rows:
            out.append(
                SessionRecord(
                    id=str(row[0]),
                    origin=str(row[1]),
                    label=cast(str | None, row[2]),
                    created_at=int(row[3]),
                    last_used_at=cast(int | None, row[4]),
                    expires_at=cast(int | None, row[5]),
                    status=cast(SessionStatus, str(row[6])),
                    state_blob=bytes(row[7]),
                    metadata=str(row[8]),
                )
            )
        return out

    async def update_session_status(self, session_id: str, status: str) -> None:
        sql = "UPDATE sessions SET status = ? WHERE id = ?"
        await self._enqueue_write(sql, (status, session_id))

    async def update_session_state_blob(self, session_id: str, state_blob: bytes) -> None:
        """Overwrite the encrypted state blob and bump ``last_used_at``.

        Used by the session-close write-back path (ADR-018) to persist
        policy-admitted cookie deltas from a just-finished agent session.
        """
        sql = "UPDATE sessions SET state_blob = ?, last_used_at = ? WHERE id = ?"
        await self._enqueue_write(sql, (state_blob, int(time.time()), session_id))

    async def revoke_session(self, session_id: str) -> None:
        sql = "UPDATE sessions SET status = 'revoked', state_blob = ? WHERE id = ?"
        await self._enqueue_write(sql, (b"", session_id))

    async def insert_token(self, token_hash: str, name: str, expires_at: int) -> None:
        now = int(time.time())
        sql = """
            INSERT INTO api_tokens (token_hash, name, created_at, last_used_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
        """
        await self._enqueue_write(sql, (token_hash, name, now, None, expires_at))

    async def verify_token(self, token: str) -> TokenRecord | None:
        digest = hash_token(token)
        conn = self._require_conn()

        def work() -> sqlite3.Row | tuple[Any, ...] | None:
            cur = conn.execute(
                """
                SELECT token_hash, name, created_at, last_used_at, expires_at
                FROM api_tokens WHERE token_hash = ?
                """,
                (digest,),
            )
            row = cur.fetchone()
            cur.close()
            return row

        row = await self._run_sync(work)
        if row is None:
            return None
        return TokenRecord(
            token_hash=str(row[0]),
            name=str(row[1]),
            created_at=int(row[2]),
            last_used_at=cast(int | None, row[3]),
            expires_at=int(row[4]),
        )

    async def touch_token_last_used(self, token_hash: str, now: int) -> None:
        sql = "UPDATE api_tokens SET last_used_at = ? WHERE token_hash = ?"
        await self._enqueue_write(sql, (now, token_hash))

    async def delete_token(self, token_hash: str) -> None:
        sql = "DELETE FROM api_tokens WHERE token_hash = ?"
        await self._enqueue_write(sql, (token_hash,))

    async def list_tokens(self) -> list[TokenRecord]:
        conn = self._require_conn()

        def work() -> list[sqlite3.Row | tuple[Any, ...]]:
            cur = conn.execute(
                """
                SELECT token_hash, name, created_at, last_used_at, expires_at
                FROM api_tokens ORDER BY created_at ASC
                """,
            )
            rows = cur.fetchall()
            cur.close()
            return list(rows)

        rows = await self._run_sync(work)
        out: list[TokenRecord] = []
        for row in rows:
            out.append(
                TokenRecord(
                    token_hash=str(row[0]),
                    name=str(row[1]),
                    created_at=int(row[2]),
                    last_used_at=cast(int | None, row[3]),
                    expires_at=int(row[4]),
                )
            )
        return out

    async def insert_audit(self, entry: AuditEntry) -> None:
        sql = """
            INSERT INTO audit_log (timestamp, session_id, agent_id, event_type, origin, detail)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        args = (
            entry.timestamp,
            entry.session_id,
            entry.agent_id,
            entry.event_type,
            entry.origin,
            entry.detail,
        )
        await self._enqueue_write(sql, args)

    async def query_audit(self, since: int | None, limit: int) -> list[AuditEntry]:
        conn = self._require_conn()

        def work() -> list[sqlite3.Row | tuple[Any, ...]]:
            if since is None:
                cur = conn.execute(
                    """
                    SELECT id, timestamp, session_id, agent_id, event_type, origin, detail
                    FROM audit_log ORDER BY timestamp DESC LIMIT ?
                    """,
                    (limit,),
                )
            else:
                cur = conn.execute(
                    """
                    SELECT id, timestamp, session_id, agent_id, event_type, origin, detail
                    FROM audit_log WHERE timestamp >= ?
                    ORDER BY timestamp DESC LIMIT ?
                    """,
                    (since, limit),
                )
            rows = cur.fetchall()
            cur.close()
            return list(rows)

        rows = await self._run_sync(work)
        return [
            AuditEntry(
                id=int(row[0]),
                timestamp=int(row[1]),
                session_id=cast(str | None, row[2]),
                agent_id=cast(str | None, row[3]),
                event_type=str(row[4]),
                origin=cast(str | None, row[5]),
                detail=str(row[6]),
            )
            for row in rows
        ]

    async def get_policy(self, origin: str) -> PolicyRecord | None:
        conn = self._require_conn()

        def work() -> sqlite3.Row | tuple[Any, ...] | None:
            cur = conn.execute(
                "SELECT origin, yaml_body, updated_at FROM policies WHERE origin = ?",
                (origin,),
            )
            row = cur.fetchone()
            cur.close()
            return row

        row = await self._run_sync(work)
        if row is None:
            return None
        return PolicyRecord(origin=str(row[0]), yaml_body=str(row[1]), updated_at=int(row[2]))

    async def insert_review(self, review: ReviewRecord) -> None:
        sql = """
            INSERT INTO pending_reviews (
                id, session_handle, session_id, agent_id,
                action_type, action_detail, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        await self._enqueue_write(
            sql,
            (
                review.id,
                review.session_handle,
                review.session_id,
                review.agent_id,
                review.action_type,
                review.action_detail,
                review.status,
                review.created_at,
            ),
        )

    async def get_review(self, review_id: str) -> ReviewRecord | None:
        conn = self._require_conn()

        def work() -> sqlite3.Row | tuple[Any, ...] | None:
            cur = conn.execute(
                """
                SELECT id, session_handle, session_id, agent_id,
                       action_type, action_detail, status,
                       created_at, decided_at, decided_by
                FROM pending_reviews WHERE id = ?
                """,
                (review_id,),
            )
            row = cur.fetchone()
            cur.close()
            return row

        row = await self._run_sync(work)
        if row is None:
            return None
        return ReviewRecord(
            id=str(row[0]),
            session_handle=str(row[1]),
            session_id=str(row[2]),
            agent_id=cast(str | None, row[3]),
            action_type=str(row[4]),
            action_detail=str(row[5]),
            status=cast(ReviewStatus, str(row[6])),
            created_at=int(row[7]),
            decided_at=cast(int | None, row[8]),
            decided_by=cast(str | None, row[9]),
        )

    async def list_pending_reviews(self) -> list[ReviewRecord]:
        conn = self._require_conn()

        def work() -> list[sqlite3.Row | tuple[Any, ...]]:
            cur = conn.execute(
                """
                SELECT id, session_handle, session_id, agent_id,
                       action_type, action_detail, status,
                       created_at, decided_at, decided_by
                FROM pending_reviews WHERE status = 'pending'
                ORDER BY created_at ASC
                """
            )
            rows = cur.fetchall()
            cur.close()
            return list(rows)

        rows = await self._run_sync(work)
        return [
            ReviewRecord(
                id=str(r[0]),
                session_handle=str(r[1]),
                session_id=str(r[2]),
                agent_id=cast(str | None, r[3]),
                action_type=str(r[4]),
                action_detail=str(r[5]),
                status=cast(ReviewStatus, str(r[6])),
                created_at=int(r[7]),
                decided_at=cast(int | None, r[8]),
                decided_by=cast(str | None, r[9]),
            )
            for r in rows
        ]

    async def decide_review(
        self,
        review_id: str,
        *,
        status: ReviewStatus,
        decided_by: str,
        now: int,
    ) -> None:
        sql = """
            UPDATE pending_reviews
            SET status = ?, decided_at = ?, decided_by = ?
            WHERE id = ? AND status = 'pending'
        """
        await self._enqueue_write(sql, (status, now, decided_by, review_id))

    async def upsert_policy(self, origin: str, yaml_body: str) -> None:
        now = int(time.time())
        sql = """
            INSERT INTO policies (origin, yaml_body, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(origin) DO UPDATE SET
              yaml_body = excluded.yaml_body,
              updated_at = excluded.updated_at
        """
        await self._enqueue_write(sql, (origin, yaml_body, now))

    def _require_conn(self) -> sqlcipher.Connection:
        if self._conn is None:
            raise VaultError("Vault connection is not open.")
        return self._conn

    async def _connect(self, key: bytearray) -> None:
        pragma_literal = format_sqlcipher_hex_pragma_key(key)

        def open_conn() -> sqlcipher.Connection:
            conn = sqlcipher.connect(str(self._path))
            conn.execute(f'PRAGMA key = "{pragma_literal}"')
            conn.execute("SELECT 1")
            return conn

        try:
            self._conn = await self._run_sync(open_conn)
        except (sqlite3.Error, sqlcipher.Error) as exc:
            self._conn = None
            raise VaultLockedError("Incorrect passphrase or vault corrupted.") from exc

    async def _dispose_connection_only(self) -> None:
        """Tear-down path for errors during ``open``/``initialize``."""
        if self._conn is not None:
            conn = self._conn
            self._conn = None

            def close_conn() -> None:
                conn.close()

            try:
                await self._run_sync(close_conn)
            except VaultError:
                # Worker thread already gone — best-effort close in this thread.
                with contextlib.suppress(Exception):
                    conn.close()
        await self._stop_worker()

    async def _apply_pending_migrations(self) -> None:
        conn = self._require_conn()

        def migrate() -> None:
            applied = _sync_applied_versions(conn)
            for version, path in _migration_files():
                if version in applied:
                    continue
                sql_text = path.read_text(encoding="utf-8")
                try:
                    conn.executescript(sql_text)
                    conn.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (version, int(time.time())),
                    )
                    conn.commit()
                except (sqlite3.Error, sqlcipher.Error) as exc:
                    raise VaultMigrationError(f"Migration {version} ({path.name}) failed.") from exc

        await self._run_sync(migrate)

    async def _upsert_crypto_meta(self, meta: PlaintextVaultMeta) -> None:
        conn = self._require_conn()

        def work() -> None:
            conn.execute(
                """
                INSERT INTO vault_metadata (
                    id, salt, argon2_memory_cost, argon2_time_cost,
                    argon2_parallelism, argon2_hash_len
                ) VALUES (1, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    salt = excluded.salt,
                    argon2_memory_cost = excluded.argon2_memory_cost,
                    argon2_time_cost = excluded.argon2_time_cost,
                    argon2_parallelism = excluded.argon2_parallelism,
                    argon2_hash_len = excluded.argon2_hash_len
                """,
                (
                    meta.salt,
                    meta.params.memory_cost,
                    meta.params.time_cost,
                    meta.params.parallelism,
                    meta.params.hash_len,
                ),
            )
            conn.commit()

        await self._run_sync(work)

    async def _verify_encrypted_meta(self, expected: PlaintextVaultMeta) -> None:
        conn = self._require_conn()

        def verify() -> None:
            cur = conn.execute(
                """
                SELECT salt, argon2_memory_cost, argon2_time_cost,
                       argon2_parallelism, argon2_hash_len
                FROM vault_metadata WHERE id = 1
                """,
            )
            row = cur.fetchone()
            cur.close()
            if row is None:
                raise VaultIntegrityError("vault_metadata row missing.")
            salt_db = bytes(row[0])
            if (
                salt_db != expected.salt
                or int(row[1]) != expected.params.memory_cost
                or int(row[2]) != expected.params.time_cost
                or int(row[3]) != expected.params.parallelism
                or int(row[4]) != expected.params.hash_len
            ):
                raise VaultIntegrityError("Encrypted vault metadata does not match plaintext meta.")

        await self._run_sync(verify)

    async def _enqueue_write(self, sql: str, args: Iterable[Any]) -> None:
        """Submit a mutating SQL statement to the worker thread.

        Identical scheduling to reads (single queue, single worker thread).
        SQLite serializes commits anyway; routing writes through the same
        worker is the cleanest way to keep "exactly one thread touches the
        connection" true.
        """
        conn = self._require_conn()
        materialized_args = tuple(args)

        def apply_write() -> None:
            conn.execute(sql, materialized_args)
            conn.commit()

        await self._run_sync(apply_write)


async def unlock_vault(*, home: Path, passphrase: str) -> Vault:
    """Convenience: load plaintext meta, derive key, :meth:`Vault.open`."""
    from coral.paths import vault_db_path

    meta = read_plaintext_meta(home=home)
    key = derive_key(passphrase, meta.salt, params=meta.params)
    return await Vault.open(vault_db_path(home), key, plaintext_meta=meta)


async def seed_bundled_behavior_packs(vault: Vault) -> int:
    """Load every bundled behavior pack into the ``policies`` table.

    Only inserts policies for origins that don't already have one — re-running
    is safe. Returns the number of packs newly inserted.
    """
    pack_dir = Path(__file__).resolve().parent / "behavior_packs"
    inserted = 0
    for yaml_path in sorted(pack_dir.glob("*.yaml")):
        body = yaml_path.read_text(encoding="utf-8")
        try:
            doc = json.loads(json.dumps(_safe_yaml_to_dict(body)))
        except Exception:
            continue
        origin = doc.get("__origin_hint")
        if not isinstance(origin, str):
            continue
        existing = await vault.get_policy(origin)
        if existing is not None:
            continue
        await vault.upsert_policy(origin, body)
        inserted += 1
    return inserted


def _safe_yaml_to_dict(body: str) -> dict[str, Any]:
    """Parse YAML and extract an ``__origin_hint`` from the bundled pack.

    Bundled packs document the origin in a top-level comment ``# ... origin: <url>``
    via the YAML's ``origin`` field. Fall back to the YAML's own ``origin`` key
    if present; otherwise infer from the filename.
    """
    import yaml as _yaml

    parsed_any: Any = _yaml.safe_load(body)
    if not isinstance(parsed_any, dict):
        return {}
    out: dict[str, Any] = cast(dict[str, Any], parsed_any)
    if "origin" in out and isinstance(out["origin"], str):
        out["__origin_hint"] = out["origin"]
    return out


def make_demo_session_record(*, origin: str = "https://example.com") -> SessionRecord:
    """Construct a session row with a gzipped JSON blob (tests / diagnostics)."""
    blob = compress_blob({"version": 1, "origin": origin})
    now = int(time.time())
    return SessionRecord(
        id=str(uuid.uuid4()),
        origin=origin,
        label="demo",
        created_at=now,
        last_used_at=None,
        expires_at=None,
        status="active",
        state_blob=blob,
        metadata="{}",
    )


__all__ = [
    "AuditEntry",
    "PlaintextVaultMeta",
    "PolicyRecord",
    "SessionRecord",
    "TokenRecord",
    "Vault",
    "VaultError",
    "VaultIntegrityError",
    "VaultLockedError",
    "VaultMigrationError",
    "compress_blob",
    "decompress_blob",
    "make_demo_session_record",
    "read_plaintext_meta",
    "unlock_vault",
    "write_plaintext_meta",
]
