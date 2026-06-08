"""Skeleton tests for the Coral Cloud SDK, run against the fake transport."""

from __future__ import annotations

import pytest

from coral_cloud import (
    CoralAuthError,
    CoralCloud,
    CoralConfigError,
    CoralNotFoundError,
    LoginSession,
    OpenSession,
    Session,
)
from coral_cloud._transport import Response

from .conftest import FakeTransport


def _client(fake: FakeTransport) -> CoralCloud:
    return CoralCloud(transport=fake)


def test_requires_api_key_without_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORAL_API_KEY", raising=False)
    with pytest.raises(CoralConfigError):
        CoralCloud()


def test_api_key_from_env_builds_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORAL_API_KEY", "ck_live_test")
    client = CoralCloud()  # should not raise; real httpx transport constructed
    assert client._base_url  # noqa: SLF001 - skeleton assertion


def test_list_sessions_parses_rows(fake: FakeTransport) -> None:
    fake.on(
        "GET",
        "/v1/sessions",
        200,
        {
            "sessions": [
                {
                    "id": "s1",
                    "origin": "https://github.com",
                    "status": "active",
                    "created_at": 1000,
                }
            ]
        },
    )
    sessions = _client(fake).list_sessions()
    assert len(sessions) == 1
    assert isinstance(sessions[0], Session)
    assert sessions[0].origin == "https://github.com"


def test_list_sessions_passes_filters(fake: FakeTransport) -> None:
    fake.on("GET", "/v1/sessions", 200, {"sessions": []})
    _client(fake).list_sessions(origin="https://github.com", status="active")
    req = fake.requests[-1]
    assert req.params == {"origin": "https://github.com", "status": "active"}


def test_open_session_returns_wss_cdp_url(fake: FakeTransport) -> None:
    fake.on(
        "POST",
        "/v1/sessions/s1/open",
        201,
        {
            "handle": "h1",
            "session_id": "s1",
            "cdp_url": "wss://gw.coral.dev/cdp/cdpt_abc",
            "expires_at": 9999,
            "policy_summary": {"default_action": "deny"},
        },
    )
    opened = _client(fake).open_session("s1", purpose="triage")
    assert isinstance(opened, OpenSession)
    assert opened.cdp_url.startswith("wss://")
    assert fake.requests[-1].json == {"purpose": "triage"}


def test_session_context_manager_closes(fake: FakeTransport) -> None:
    fake.on(
        "POST",
        "/v1/sessions/s1/open",
        201,
        {"handle": "h1", "session_id": "s1", "cdp_url": "wss://x/y", "expires_at": 1},
    )
    fake.on(
        "POST",
        "/v1/sessions/handles/h1/close",
        200,
        {"closed": True, "reason": "agent_closed"},
    )
    client = _client(fake)
    with client.session("s1", purpose="p") as s:
        assert s.handle == "h1"
    # The close route must have been hit.
    assert any(r.path == "/v1/sessions/handles/h1/close" for r in fake.requests)


def test_session_context_manager_closes_on_exception(fake: FakeTransport) -> None:
    fake.on(
        "POST",
        "/v1/sessions/s1/open",
        201,
        {"handle": "h1", "session_id": "s1", "cdp_url": "wss://x/y", "expires_at": 1},
    )
    fake.on("POST", "/v1/sessions/handles/h1/close", 200, {"closed": True})
    client = _client(fake)
    with pytest.raises(RuntimeError), client.session("s1", purpose="p"):
        raise RuntimeError("boom")
    assert any(r.path == "/v1/sessions/handles/h1/close" for r in fake.requests)


def test_401_raises_auth_error(fake: FakeTransport) -> None:
    fake.on("GET", "/v1/sessions", 401, {"error": "invalid_token"})
    with pytest.raises(CoralAuthError) as exc:
        _client(fake).list_sessions()
    assert exc.value.status_code == 401
    assert exc.value.code == "invalid_token"


def test_404_raises_not_found(fake: FakeTransport) -> None:
    fake.on("GET", "/v1/logins/nope", 404, {"error": "not_found"})
    with pytest.raises(CoralNotFoundError):
        _client(fake).get_login("nope")


def test_start_login_returns_live_view_url(fake: FakeTransport) -> None:
    fake.on(
        "POST",
        "/v1/logins",
        201,
        {
            "login_id": "l1",
            "origin": "https://github.com",
            "status": "awaiting_user",
            "live_view_url": "https://app.coral.dev/login/l1",
        },
    )
    login = _client(fake).start_login("https://github.com")
    assert isinstance(login, LoginSession)
    assert login.live_view_url.endswith("/login/l1")
    assert not login.is_terminal


def test_wait_for_login_returns_when_captured(fake: FakeTransport) -> None:
    fake.on(
        "GET",
        "/v1/logins/l1",
        200,
        {
            "login_id": "l1",
            "origin": "https://github.com",
            "status": "captured",
            "live_view_url": "https://app.coral.dev/login/l1",
            "session_id": "s9",
        },
    )
    login = _client(fake).wait_for_login("l1", timeout=1, poll_interval=0)
    assert login.status == "captured"
    assert login.session_id == "s9"


def test_list_policies_parses_rows(fake: FakeTransport) -> None:
    fake.on(
        "GET",
        "/v1/policies",
        200,
        {
            "policies": [
                {"origin": "https://github.com", "yaml_body": "x: 1", "updated_at": 1},
                {"origin": "https://gmail.com", "yaml_body": "y: 2", "updated_at": 2},
            ]
        },
    )
    policies = _client(fake).list_policies()
    assert {p.origin for p in policies} == {"https://github.com", "https://gmail.com"}


def test_origin_is_url_encoded_in_policy_path(fake: FakeTransport) -> None:
    # The route is registered with the encoded path; if encoding is wrong the
    # fake returns 404 and model_validate fails.
    fake.routes[("GET", "/v1/policies/https%3A%2F%2Fgithub.com")] = Response(
        200, {"origin": "https://github.com", "yaml_body": "x: 1", "updated_at": 1}
    )
    policy = _client(fake).get_policy("https://github.com")
    assert policy.origin == "https://github.com"


def test_client_context_manager_closes_transport(fake: FakeTransport) -> None:
    with CoralCloud(transport=fake):
        pass
    assert fake.closed is True
