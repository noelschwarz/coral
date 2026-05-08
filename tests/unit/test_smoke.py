from importlib import metadata

import pytest

import coral
from coral.crypto import TEST_PARAMS
from coral.http_api import build_http_app
from coral.mcp_server import build_mcp_server
from coral.models import schema_table_names
from coral.paths import coral_home
from coral.vault import Vault, unlock_vault


def test_package_version_matches_metadata() -> None:
    assert coral.__version__ == metadata.version("coral")


def test_schema_table_names_match_spec() -> None:
    assert schema_table_names() == ["sessions", "policies", "audit_log", "api_tokens"]


def test_coral_home_respects_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    home = tmp_path_factory.mktemp("coralhome")
    monkeypatch.setenv("CORAL_HOME", str(home))
    assert coral_home() == home


def test_healthz_via_asgi() -> None:
    from fastapi.testclient import TestClient

    client = TestClient(build_http_app())
    r = client.get("/healthz")
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "ok"
    assert payload["version"] == coral.__version__


@pytest.mark.asyncio
async def test_init_vault_round_trip(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    home = tmp_path_factory.mktemp("vault")
    monkeypatch.setenv("CORAL_HOME", str(home))
    passphrase = "correct horse battery staple"
    vault = await Vault.initialize(home, passphrase, params=TEST_PARAMS)
    await vault.close()
    vault2 = await unlock_vault(home=home, passphrase=passphrase)
    await vault2.close()


def test_mcp_streamable_http_initialize() -> None:
    from starlette.testclient import TestClient

    app = build_mcp_server().streamable_http_app()
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "coral-pytest", "version": "0.0.0"},
        },
    }
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            json=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == 1
        assert "result" in data
        assert data["result"]["serverInfo"]["name"] == "coral"
