"""Unit tests for :mod:`coral.vault`."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from coral.crypto import TEST_PARAMS, derive_key, generate_token, hash_token
from coral.models import AuditEntry
from coral.paths import vault_db_path
from coral.vault import (
    Vault,
    VaultIntegrityError,
    VaultLockedError,
    compress_blob,
    decompress_blob,
    make_demo_session_record,
    read_plaintext_meta,
    unlock_vault,
)


@pytest.mark.asyncio
async def test_round_trip_session_blob(tmp_path_factory: pytest.TempPathFactory) -> None:
    home = tmp_path_factory.mktemp("vault1")
    passphrase = "correct horse battery staple"
    vault = await Vault.initialize(home, passphrase, params=TEST_PARAMS)
    rec = make_demo_session_record()
    await vault.insert_session(rec)
    await vault.close()

    vault2 = await unlock_vault(home=home, passphrase=passphrase)
    got = await vault2.get_session(rec.id)
    assert got is not None
    assert got.state_blob == rec.state_blob
    await vault2.close()


@pytest.mark.asyncio
async def test_wrong_passphrase_raises(tmp_path_factory: pytest.TempPathFactory) -> None:
    home = tmp_path_factory.mktemp("vault2")
    passphrase = "correct horse battery staple"
    vault = await Vault.initialize(home, passphrase, params=TEST_PARAMS)
    await vault.close()

    meta = read_plaintext_meta(home=home)
    bad_key = derive_key("wrong-passphrase-here", meta.salt, params=meta.params)
    with pytest.raises(VaultLockedError):
        await Vault.open(vault_db_path(home), bad_key, plaintext_meta=meta)


@pytest.mark.asyncio
async def test_migration_not_reapplied(tmp_path_factory: pytest.TempPathFactory) -> None:
    home = tmp_path_factory.mktemp("vault3")
    passphrase = "correct horse battery staple"
    v1 = await Vault.initialize(home, passphrase, params=TEST_PARAMS)
    await v1.close()

    v2 = await unlock_vault(home=home, passphrase=passphrase)

    def migration_rows() -> int:
        conn = v2._require_conn()
        cur = conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = 1")
        row = cur.fetchone()
        cur.close()
        return int(row[0]) if row else 0

    row = await v2._run_sync(migration_rows)
    assert row == 1
    await v2.close()


@pytest.mark.asyncio
async def test_concurrent_writes_serialized(tmp_path_factory: pytest.TempPathFactory) -> None:
    home = tmp_path_factory.mktemp("vault4")
    passphrase = "correct horse battery staple"
    vault = await Vault.initialize(home, passphrase, params=TEST_PARAMS)

    async def insert_one(i: int) -> None:
        entry = AuditEntry(
            timestamp=int(time.time()) + i,
            session_id=None,
            agent_id=None,
            event_type="test.write",
            origin=None,
            detail=json.dumps({"i": i}),
        )
        await vault.insert_audit(entry)

    await asyncio.gather(*(insert_one(i) for i in range(100)))

    rows = await vault.query_audit(since=None, limit=200)
    assert len(rows) == 100
    await vault.close()


@pytest.mark.asyncio
async def test_audit_append_only_surface(tmp_path_factory: pytest.TempPathFactory) -> None:
    home = tmp_path_factory.mktemp("vault5")
    vault = await Vault.initialize(home, "correct horse battery staple", params=TEST_PARAMS)
    assert not hasattr(vault, "delete_audit")
    await vault.close()


@pytest.mark.asyncio
async def test_token_verify_round_trip(tmp_path_factory: pytest.TempPathFactory) -> None:
    home = tmp_path_factory.mktemp("vault6")
    vault = await Vault.initialize(home, "correct horse battery staple", params=TEST_PARAMS)
    raw = generate_token()
    await vault.insert_token(hash_token(raw), name="cli", expires_at=int(time.time()) + 3600)
    got = await vault.verify_token(raw)
    assert got is not None and got.name == "cli"
    assert await vault.verify_token(generate_token()) is None
    await vault.close()


@pytest.mark.asyncio
async def test_revoke_zeros_blob(tmp_path_factory: pytest.TempPathFactory) -> None:
    home = tmp_path_factory.mktemp("vault7")
    vault = await Vault.initialize(home, "correct horse battery staple", params=TEST_PARAMS)
    rec = make_demo_session_record()
    await vault.insert_session(rec)
    await vault.revoke_session(rec.id)
    got = await vault.get_session(rec.id)
    assert got is not None
    assert got.status == "revoked"
    assert got.state_blob == b""
    await vault.close()


@pytest.mark.asyncio
async def test_policy_upsert_single_row(tmp_path_factory: pytest.TempPathFactory) -> None:
    home = tmp_path_factory.mktemp("vault8")
    vault = await Vault.initialize(home, "correct horse battery staple", params=TEST_PARAMS)
    await vault.upsert_policy("https://a.example", "a: 1")
    await vault.upsert_policy("https://a.example", "a: 2")
    pol = await vault.get_policy("https://a.example")
    assert pol is not None
    assert pol.yaml_body == "a: 2"
    await vault.close()


@pytest.mark.asyncio
async def test_encrypted_meta_mismatch_raises(tmp_path_factory: pytest.TempPathFactory) -> None:
    home = tmp_path_factory.mktemp("vault9")
    passphrase = "correct horse battery staple"
    vault = await Vault.initialize(home, passphrase, params=TEST_PARAMS)

    def tamper_salt() -> None:
        conn = vault._require_conn()
        conn.execute(
            "UPDATE vault_metadata SET salt = ? WHERE id = 1",
            (b"\xff" * TEST_PARAMS.salt_len,),
        )
        conn.commit()

    await vault._run_sync(tamper_salt)
    await vault.close()

    with pytest.raises(VaultIntegrityError):
        await unlock_vault(home=home, passphrase=passphrase)


def test_compress_decompress_round_trip() -> None:
    payload = {"version": 1, "cookies": [{"name": "k", "value": "v"}], "origin": "https://x"}
    assert decompress_blob(compress_blob(payload)) == payload


def test_decompress_empty_returns_empty_dict() -> None:
    assert decompress_blob(b"") == {}


def test_decompress_invalid_blob_raises() -> None:
    with pytest.raises(VaultIntegrityError):
        decompress_blob(b"not-a-gzip-stream")


def test_decompress_non_object_raises() -> None:
    import gzip

    blob = gzip.compress(b'["not-an-object"]')
    with pytest.raises(VaultIntegrityError):
        decompress_blob(blob)


@pytest.mark.asyncio
async def test_multiple_sessions_per_origin(tmp_path_factory: pytest.TempPathFactory) -> None:
    home = tmp_path_factory.mktemp("vault_multi_origin")
    vault = await Vault.initialize(home, "correct horse battery staple", params=TEST_PARAMS)
    rec_a = make_demo_session_record(origin="https://example.com")
    rec_b = make_demo_session_record(origin="https://example.com")
    await vault.insert_session(rec_a)
    await vault.insert_session(rec_b)
    rows = await vault.list_sessions()
    assert {r.id for r in rows} == {rec_a.id, rec_b.id}
    await vault.close()


@pytest.mark.asyncio
async def test_migration_table_present_after_init(tmp_path_factory: pytest.TempPathFactory) -> None:
    home = tmp_path_factory.mktemp("vault10")
    vault = await Vault.initialize(home, "correct horse battery staple", params=TEST_PARAMS)

    def table_names() -> set[str]:
        conn = vault._require_conn()
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        names = {str(r[0]) for r in cur.fetchall()}
        cur.close()
        return names

    names = await vault._run_sync(table_names)
    await vault.close()
    assert "schema_migrations" in names
    assert "vault_metadata" in names
