"""Encrypted vault (SQLCipher) with serialized writes.

SQLite ships a single writer; HTTP/MCP handlers may enqueue writes concurrently.
Route mutations through :meth:`Vault._enqueue_write` so ordering stays predictable.

Reads may bypass the queue, but every SQLCipher call runs on a dedicated
single-worker executor thread so the underlying connection never hops threads.

Do **not** replace the write queue with a naive asyncio.Lock: it hides queue depth
and complicates shutdown compared to an explicit writer task.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from base64 import b64decode, b64encode
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
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
from coral.models import AuditEntry, PolicyRecord, SessionRecord, SessionStatus, TokenRecord

_WRITER_STOP: Final = object()

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


class _WriteCmd(NamedTuple):
    sql: str
    args: tuple[Any, ...]
    fut: asyncio.Future[None]


def _zero_bytearray(buf: bytearray) -> None:
    buf[:] = b"\x00" * len(buf)


def _compress_blob(data: dict[str, Any]) -> bytes:
    import gzip

    return gzip.compress(json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def _decompress_blob(blob: bytes) -> dict[str, Any]:
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
    """Async façade over a synchronous SQLCipher connection (single worker thread)."""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="coral-vault")
        self._conn: sqlcipher.Connection | None = None
        self._queue: asyncio.Queue[_WriteCmd | object] = asyncio.Queue()
        self._writer_task: asyncio.Task[None] | None = None

    async def _run_sync(self, fn: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, fn)

    async def _shutdown_executor_pool(self) -> None:
        await asyncio.to_thread(self._executor.shutdown, True)

    @classmethod
    async def open(cls, path: Path, key: bytearray, *, plaintext_meta: PlaintextVaultMeta) -> Vault:
        """Unlock an existing vault and verify encrypted metadata."""
        self = cls(path)
        await self._connect(key)
        try:
            await self._apply_pending_migrations()
            await self._verify_encrypted_meta(plaintext_meta)
        except VaultError:
            await self._dispose_connection_only()
            raise
        self._start_writer()
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
        try:
            await self._connect(key)
            await self._apply_pending_migrations()
            await self._upsert_crypto_meta(meta)
            await self._verify_encrypted_meta(meta)
        except VaultError:
            await self._dispose_connection_only()
            raise
        self._start_writer()
        _zero_bytearray(key)
        return self

    async def close(self) -> None:
        if self._writer_task is not None:
            await self._queue.put(_WRITER_STOP)
            await self._writer_task
            self._writer_task = None
        if self._conn is not None:
            conn = self._conn
            self._conn = None

            def close_conn() -> None:
                conn.close()

            await self._run_sync(close_conn)
        await self._shutdown_executor_pool()

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
        if self._writer_task is not None:
            await self._queue.put(_WRITER_STOP)
            await self._writer_task
            self._writer_task = None
        if self._conn is not None:
            conn = self._conn
            self._conn = None

            def close_conn() -> None:
                conn.close()

            await self._run_sync(close_conn)
        await self._shutdown_executor_pool()

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

    def _start_writer(self) -> None:
        if self._writer_task is not None:
            raise VaultError("Writer task already running.")

        async def _runner() -> None:
            conn = self._require_conn()
            while True:
                item = await self._queue.get()
                try:
                    if item is _WRITER_STOP:
                        break
                    assert isinstance(item, _WriteCmd)

                    def apply_write(
                        _sql: str = item.sql,
                        _args: tuple[Any, ...] = item.args,
                    ) -> None:
                        conn.execute(_sql, _args)
                        conn.commit()

                    await self._run_sync(apply_write)
                    item.fut.set_result(None)
                except BaseException as exc:
                    if isinstance(item, _WriteCmd) and not item.fut.done():
                        item.fut.set_exception(exc)
                    else:
                        raise

        self._writer_task = asyncio.create_task(_runner(), name="coral-vault-writer")

    async def _enqueue_write(self, sql: str, args: Iterable[Any]) -> None:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[None] = loop.create_future()
        await self._queue.put(_WriteCmd(sql, tuple(args), fut))
        await fut


async def unlock_vault(*, home: Path, passphrase: str) -> Vault:
    """Convenience: load plaintext meta, derive key, :meth:`Vault.open`."""
    from coral.paths import vault_db_path

    meta = read_plaintext_meta(home=home)
    key = derive_key(passphrase, meta.salt, params=meta.params)
    return await Vault.open(vault_db_path(home), key, plaintext_meta=meta)


def make_demo_session_record(*, origin: str = "https://example.com") -> SessionRecord:
    """Construct a session row with a gzipped JSON blob (tests / diagnostics)."""
    blob = _compress_blob({"version": 1, "origin": origin})
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
    "_compress_blob",
    "_decompress_blob",
    "make_demo_session_record",
    "read_plaintext_meta",
    "unlock_vault",
    "write_plaintext_meta",
]
