"""Bearer-token middleware tests (spec §5.1, §6.2)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import httpx
import pytest

from coral.crypto import TEST_PARAMS, generate_token, hash_token
from coral.http_api import HandshakeState, build_http_app
from coral.vault import Vault


def _config_stub() -> object:
    return type(
        "Cfg",
        (),
        {"extension_token_ttl_seconds": 60, "cli_token_ttl_seconds": 60},
    )()


async def _open_vault(home_path) -> Vault:
    return await Vault.initialize(home_path, "correct horse battery staple", params=TEST_PARAMS)


async def _make_client(vault: Vault) -> httpx.AsyncClient:
    app = build_http_app(
        vault=vault,
        handshake_state=HandshakeState(challenge="DUMMY-CHAL-LENG-EXXX"),
        config=_config_stub(),
    )
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
async def vault_with_token(
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncIterator[tuple[Vault, str]]:
    home = tmp_path_factory.mktemp("auth_vault")
    vault = await _open_vault(home)
    raw = generate_token()
    await vault.insert_token(hash_token(raw), name="extension", expires_at=int(time.time()) + 3600)
    try:
        yield vault, raw
    finally:
        await vault.close()


@pytest.fixture
async def vault_with_expired_token(
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncIterator[tuple[Vault, str]]:
    home = tmp_path_factory.mktemp("auth_vault_expired")
    vault = await _open_vault(home)
    raw = generate_token()
    await vault.insert_token(hash_token(raw), name="extension", expires_at=int(time.time()) - 60)
    try:
        yield vault, raw
    finally:
        await vault.close()


async def test_missing_authorization_header_returns_401(
    vault_with_token: tuple[Vault, str],
) -> None:
    vault, _ = vault_with_token
    async with await _make_client(vault) as client:
        r = await client.get("/sessions")
    assert r.status_code == 401
    assert r.json()["error"] == "missing_authorization"


async def test_wrong_scheme_returns_401(vault_with_token: tuple[Vault, str]) -> None:
    vault, _ = vault_with_token
    async with await _make_client(vault) as client:
        r = await client.get("/sessions", headers={"Authorization": "Basic abc"})
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_authorization_scheme"


async def test_malformed_bearer_returns_401(vault_with_token: tuple[Vault, str]) -> None:
    vault, _ = vault_with_token
    async with await _make_client(vault) as client:
        r = await client.get("/sessions", headers={"Authorization": "Bearer "})
    assert r.status_code == 401


async def test_unknown_token_returns_401_and_audits(vault_with_token: tuple[Vault, str]) -> None:
    vault, _ = vault_with_token
    async with await _make_client(vault) as client:
        r = await client.get("/sessions", headers={"Authorization": f"Bearer {generate_token()}"})
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_token"
    rows = await vault.query_audit(since=None, limit=10)
    assert any(r.event_type == "auth.failed" for r in rows)


async def test_expired_token_returns_401_and_audits(
    vault_with_expired_token: tuple[Vault, str],
) -> None:
    vault, raw = vault_with_expired_token
    async with await _make_client(vault) as client:
        r = await client.get("/sessions", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 401
    assert r.json()["error"] == "token_expired"
    rows = await vault.query_audit(since=None, limit=10)
    assert any(r.event_type == "auth.failed" and "token_expired" in r.detail for r in rows)


async def test_valid_token_authenticates_and_touches_last_used(
    vault_with_token: tuple[Vault, str],
) -> None:
    vault, raw = vault_with_token
    async with await _make_client(vault) as client:
        r = await client.get("/sessions", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200
    assert "sessions" in r.json()
    rec = await vault.verify_token(raw)
    assert rec is not None
    assert rec.last_used_at is not None
