"""HTTP API tests for the reviews surface (Track E)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import httpx
import pytest

from coral.crypto import TEST_PARAMS, generate_token, hash_token
from coral.http_api import HandshakeState, build_http_app
from coral.models import ReviewRecord
from coral.vault import Vault


def _config_stub() -> object:
    return type(
        "Cfg",
        (),
        {"extension_token_ttl_seconds": 60, "cli_token_ttl_seconds": 60},
    )()


@pytest.fixture
async def authed_client(
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncIterator[tuple[Vault, httpx.AsyncClient, str]]:
    home = tmp_path_factory.mktemp("reviews_vault")
    vault = await Vault.initialize(home, "correct horse battery staple", params=TEST_PARAMS)
    app = build_http_app(
        vault=vault,
        handshake_state=HandshakeState(challenge="UNUSED"),
        config=_config_stub(),
    )
    raw = generate_token()
    await vault.insert_token(hash_token(raw), name="cli", expires_at=int(time.time()) + 60)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        try:
            yield vault, c, raw
        finally:
            await vault.close()


async def _insert_pending(vault: Vault, *, agent_id: str = "agent-1") -> str:
    review = ReviewRecord(
        id=f"rev-{int(time.time() * 1000)}",
        session_handle="hdl-1",
        session_id="sess-1",
        agent_id=agent_id,
        action_type="post_content",
        action_detail='{"target":"/feed/"}',
        status="pending",
        created_at=int(time.time()),
    )
    await vault.insert_review(review)
    return review.id


async def test_list_reviews_empty(
    authed_client: tuple[Vault, httpx.AsyncClient, str],
) -> None:
    _, c, raw = authed_client
    r = await c.get("/reviews", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200
    assert r.json() == {"reviews": []}


async def test_list_reviews_returns_pending(
    authed_client: tuple[Vault, httpx.AsyncClient, str],
) -> None:
    vault, c, raw = authed_client
    rid = await _insert_pending(vault)
    r = await c.get("/reviews", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200
    ids = [x["id"] for x in r.json()["reviews"]]
    assert rid in ids


async def test_decide_review_approved(
    authed_client: tuple[Vault, httpx.AsyncClient, str],
) -> None:
    vault, c, raw = authed_client
    rid = await _insert_pending(vault)
    res = await c.post(
        f"/reviews/{rid}/decision",
        json={"decision": "approved"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert res.status_code == 204
    again = await vault.get_review(rid)
    assert again is not None
    assert again.status == "approved"


async def test_decide_review_denied(
    authed_client: tuple[Vault, httpx.AsyncClient, str],
) -> None:
    vault, c, raw = authed_client
    rid = await _insert_pending(vault)
    res = await c.post(
        f"/reviews/{rid}/decision",
        json={"decision": "denied"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert res.status_code == 204
    again = await vault.get_review(rid)
    assert again is not None
    assert again.status == "denied"


async def test_decide_review_not_found(
    authed_client: tuple[Vault, httpx.AsyncClient, str],
) -> None:
    _, c, raw = authed_client
    res = await c.post(
        "/reviews/does-not-exist/decision",
        json={"decision": "approved"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert res.status_code == 404


async def test_decide_review_idempotent_409(
    authed_client: tuple[Vault, httpx.AsyncClient, str],
) -> None:
    vault, c, raw = authed_client
    rid = await _insert_pending(vault)
    first = await c.post(
        f"/reviews/{rid}/decision",
        json={"decision": "approved"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert first.status_code == 204
    second = await c.post(
        f"/reviews/{rid}/decision",
        json={"decision": "approved"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert second.status_code == 409
