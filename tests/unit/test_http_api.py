"""Endpoint-level HTTP API tests (spec §5.1)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import httpx
import pytest

from coral.crypto import TEST_PARAMS, generate_token, hash_token
from coral.http_api import HandshakeState, build_http_app, is_chrome_extension_origin
from coral.vault import Vault


def _config_stub() -> object:
    return type(
        "Cfg",
        (),
        {"extension_token_ttl_seconds": 60, "cli_token_ttl_seconds": 60},
    )()


async def _vault(home_path) -> Vault:
    return await Vault.initialize(home_path, "correct horse battery staple", params=TEST_PARAMS)


async def _client(vault: Vault, *, challenge: str) -> tuple[httpx.AsyncClient, HandshakeState]:
    state = HandshakeState(challenge=challenge, rate_limit_per_minute=5)
    app = build_http_app(vault=vault, handshake_state=state, config=_config_stub())
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver"), state


async def _bootstrap_token(client: httpx.AsyncClient, challenge: str) -> str:
    r = await client.post(
        "/auth/handshake",
        json={"challenge": challenge, "client_name": "extension"},
    )
    assert r.status_code == 200, r.text
    return str(r.json()["token"])


@pytest.fixture
async def fresh_vault(
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncIterator[Vault]:
    home = tmp_path_factory.mktemp("api_vault")
    vault = await _vault(home)
    try:
        yield vault
    finally:
        await vault.close()


# Handshake -----------------------------------------------------------------


async def test_healthz_is_unauthenticated(fresh_vault: Vault) -> None:
    client, _ = await _client(fresh_vault, challenge="ABCD-EFGH-JKLM-NPQR")
    async with client:
        r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_handshake_wrong_challenge_returns_401(fresh_vault: Vault) -> None:
    client, _ = await _client(fresh_vault, challenge="ABCD-EFGH-JKLM-NPQR")
    async with client:
        r = await client.post("/auth/handshake", json={"challenge": "WRONGGGG", "client_name": "x"})
    assert r.status_code == 401
    rows = await fresh_vault.query_audit(since=None, limit=10)
    assert any(r.event_type == "auth.handshake.failed" for r in rows)


async def test_handshake_success_returns_token_and_authenticates(fresh_vault: Vault) -> None:
    challenge = "ABCD-EFGH-JKLM-NPQR"
    client, _ = await _client(fresh_vault, challenge=challenge)
    async with client:
        r = await client.post(
            "/auth/handshake",
            json={"challenge": challenge, "client_name": "extension"},
        )
        assert r.status_code == 200
        token = r.json()["token"]
        assert r.json()["expires_at"] > int(time.time())
        r2 = await client.get("/sessions", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200


async def test_handshake_is_single_use(fresh_vault: Vault) -> None:
    challenge = "ABCD-EFGH-JKLM-NPQR"
    client, _ = await _client(fresh_vault, challenge=challenge)
    async with client:
        r1 = await client.post("/auth/handshake", json={"challenge": challenge, "client_name": "x"})
        assert r1.status_code == 200
        r2 = await client.post("/auth/handshake", json={"challenge": challenge, "client_name": "x"})
        assert r2.status_code == 401


async def test_handshake_rate_limit(fresh_vault: Vault) -> None:
    client, state = await _client(fresh_vault, challenge="ABCD-EFGH-JKLM-NPQR")
    state.rate_limit_per_minute = 3
    async with client:
        statuses = []
        for _ in range(5):
            r = await client.post(
                "/auth/handshake", json={"challenge": "WRONG", "client_name": "x"}
            )
            statuses.append(r.status_code)
    assert statuses[:3] == [401, 401, 401]
    assert statuses[3] == 429


# Sessions ------------------------------------------------------------------


async def test_post_session_valid(fresh_vault: Vault) -> None:
    challenge = "ABCD-EFGH-JKLM-NPQR"
    client, _ = await _client(fresh_vault, challenge=challenge)
    async with client:
        token = await _bootstrap_token(client, challenge)
        body = {
            "origin": "https://example.com",
            "label": "demo",
            "state": {
                "version": 1,
                "cookies": [{"name": "sid", "value": "abc", "expires": int(time.time()) + 7200}],
                "local_storage": {"k": "v"},
                "session_storage": {},
            },
        }
        r = await client.post("/sessions", json=body, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "active"
    assert data["expires_at"] is not None


async def test_post_session_malformed_origin(fresh_vault: Vault) -> None:
    challenge = "ABCD-EFGH-JKLM-NPQR"
    client, _ = await _client(fresh_vault, challenge=challenge)
    async with client:
        token = await _bootstrap_token(client, challenge)
        r = await client.post(
            "/sessions",
            json={
                "origin": "https://example.com/path",
                "state": {"version": 1, "cookies": []},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 422


async def test_get_sessions_excludes_state_blob(fresh_vault: Vault) -> None:
    challenge = "ABCD-EFGH-JKLM-NPQR"
    client, _ = await _client(fresh_vault, challenge=challenge)
    async with client:
        token = await _bootstrap_token(client, challenge)
        await client.post(
            "/sessions",
            json={"origin": "https://a.example", "state": {"version": 1, "cookies": []}},
            headers={"Authorization": f"Bearer {token}"},
        )
        r = await client.get("/sessions", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["sessions"]) == 1
    assert "state_blob" not in body["sessions"][0]


async def test_delete_nonexistent_session_404(fresh_vault: Vault) -> None:
    challenge = "ABCD-EFGH-JKLM-NPQR"
    client, _ = await _client(fresh_vault, challenge=challenge)
    async with client:
        token = await _bootstrap_token(client, challenge)
        r = await client.delete(
            "/sessions/does-not-exist", headers={"Authorization": f"Bearer {token}"}
        )
    assert r.status_code == 404


async def test_delete_existing_session_revokes_and_zeroes(fresh_vault: Vault) -> None:
    challenge = "ABCD-EFGH-JKLM-NPQR"
    client, _ = await _client(fresh_vault, challenge=challenge)
    async with client:
        token = await _bootstrap_token(client, challenge)
        captured = await client.post(
            "/sessions",
            json={"origin": "https://b.example", "state": {"version": 1, "cookies": []}},
            headers={"Authorization": f"Bearer {token}"},
        )
        sid = captured.json()["session_id"]
        r = await client.delete(f"/sessions/{sid}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 204
    rec = await fresh_vault.get_session(sid)
    assert rec is not None
    assert rec.status == "revoked"
    assert rec.state_blob == b""


# Policies ------------------------------------------------------------------


async def test_get_policy_404(fresh_vault: Vault) -> None:
    challenge = "ABCD-EFGH-JKLM-NPQR"
    client, _ = await _client(fresh_vault, challenge=challenge)
    async with client:
        token = await _bootstrap_token(client, challenge)
        r = await client.get(
            "/policies/https%3A%2F%2Fnowhere.example",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 404


async def test_put_policy_invalid_yaml_400(fresh_vault: Vault) -> None:
    challenge = "ABCD-EFGH-JKLM-NPQR"
    client, _ = await _client(fresh_vault, challenge=challenge)
    async with client:
        token = await _bootstrap_token(client, challenge)
        r = await client.put(
            "/policies/https%3A%2F%2Fa.example",
            json={"yaml_body": "key: : invalid"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 400


async def test_put_then_get_policy(fresh_vault: Vault) -> None:
    challenge = "ABCD-EFGH-JKLM-NPQR"
    client, _ = await _client(fresh_vault, challenge=challenge)
    async with client:
        token = await _bootstrap_token(client, challenge)
        put = await client.put(
            "/policies/https%3A%2F%2Fa.example",
            json={"yaml_body": "rules:\n  - allow"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert put.status_code == 204
        get = await client.get(
            "/policies/https%3A%2F%2Fa.example",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert get.status_code == 200
    assert get.json()["yaml_body"] == "rules:\n  - allow"


# Audit ---------------------------------------------------------------------


async def test_audit_endpoint_returns_recent_events(fresh_vault: Vault) -> None:
    challenge = "ABCD-EFGH-JKLM-NPQR"
    client, _ = await _client(fresh_vault, challenge=challenge)
    async with client:
        token = await _bootstrap_token(client, challenge)
        await client.get("/sessions", headers={"Authorization": f"Bearer {token}"})
        r = await client.get("/audit?limit=50", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    types = {e["event_type"] for e in r.json()["entries"]}
    assert "auth.handshake.success" in types
    assert "session.list" in types


# CORS ----------------------------------------------------------------------


def test_cors_regex_accepts_chrome_extension() -> None:
    assert is_chrome_extension_origin("chrome-extension://abcdef0123456789ABCDEF0123456789")
    assert not is_chrome_extension_origin("http://localhost:3000")
    assert not is_chrome_extension_origin("https://example.com")


async def test_preflight_chrome_extension_allowed(fresh_vault: Vault) -> None:
    challenge = "ABCD-EFGH-JKLM-NPQR"
    client, _ = await _client(fresh_vault, challenge=challenge)
    async with client:
        r = await client.options(
            "/sessions",
            headers={
                "Origin": "chrome-extension://abcdef0123456789",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == ("chrome-extension://abcdef0123456789")


async def test_preflight_localhost_rejected(fresh_vault: Vault) -> None:
    challenge = "ABCD-EFGH-JKLM-NPQR"
    client, _ = await _client(fresh_vault, challenge=challenge)
    async with client:
        r = await client.options(
            "/sessions",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert "access-control-allow-origin" not in r.headers


# Direct-vault auth shortcut for non-handshake tests ------------------------


async def test_token_from_handshake_authenticates_subsequent_calls(
    fresh_vault: Vault,
) -> None:
    """Round-trip: handshake → token → authenticated call → audit row."""
    challenge = "ABCD-EFGH-JKLM-NPQR"
    client, _ = await _client(fresh_vault, challenge=challenge)
    async with client:
        token = await _bootstrap_token(client, challenge)
        r = await client.get("/sessions", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    rec = await fresh_vault.verify_token(token)
    assert rec is not None
    assert rec.name == "extension"


# Direct token-injection shortcut (no handshake) ----------------------------


async def test_token_directly_inserted_works(fresh_vault: Vault) -> None:
    raw = generate_token()
    await fresh_vault.insert_token(
        hash_token(raw), name="extension", expires_at=int(time.time()) + 60
    )
    client, _ = await _client(fresh_vault, challenge="UNUSED")
    async with client:
        r = await client.get("/sessions", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200
