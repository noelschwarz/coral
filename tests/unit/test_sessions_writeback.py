"""Tests for ADR-018 / PR N1: policy-gated cookie write-back on session close.

We don't launch a real Chromium for these. ``SessionServer._writeback_state``
is exercised directly with a hand-built ``OpenSession`` whose ``context`` is
a minimal fake exposing only ``cookies()``. The vault is a small fake too:
just ``get_session`` and ``update_session_state_blob``.

Helpers (``_cookie_path_allowed``, ``_glob_literal_prefix``, ``_cookies_equal``)
are pure and unit-tested separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from coral.models import SessionRecord
from coral.policy import Policy, PolicyEngine
from coral.sessions import (
    OpenSession,
    SessionServer,
    _cookie_key,
    _cookie_path_allowed,
    _cookies_equal,
    _glob_literal_prefix,
)
from coral.vault import compress_blob, decompress_blob

# ---- pure helpers -----------------------------------------------------------


def test_glob_literal_prefix_truncates_at_glob() -> None:
    assert _glob_literal_prefix("/api/v1/**") == "/api/v1/"
    assert _glob_literal_prefix("/issues") == "/issues"
    assert _glob_literal_prefix("**") == ""
    assert _glob_literal_prefix("/x?y") == "/x"
    assert _glob_literal_prefix("/x[ab]") == "/x"


def test_cookie_path_root_allowed_when_policy_has_any_path() -> None:
    assert _cookie_path_allowed("/", ["/issues/**"]) is True
    assert _cookie_path_allowed("/", []) is False


def test_cookie_path_subpath_requires_overlap() -> None:
    assert _cookie_path_allowed("/api", ["/api/v1/**"]) is True
    assert _cookie_path_allowed("/admin", ["/api/v1/**"]) is False


def test_cookie_path_exact_match_admitted() -> None:
    assert _cookie_path_allowed("/issues", ["/issues"]) is True


def test_cookies_equal_compares_value_fields() -> None:
    a = {"name": "s", "domain": ".x", "path": "/", "value": "1", "expires": 100}
    b = {"name": "s", "domain": ".x", "path": "/", "value": "1", "expires": 100}
    assert _cookies_equal(a, b)
    b["value"] = "2"
    assert not _cookies_equal(a, b)


def test_cookie_key_identity() -> None:
    assert _cookie_key({"name": "s", "domain": ".x", "path": "/"}) == ("s", ".x", "/")


# ---- _writeback_state with a fake context + fake vault ----------------------


@dataclass
class _FakeContext:
    """Minimal stand-in for ``playwright.BrowserContext``. Only ``cookies()``
    needs to work for write-back."""

    _cookies: list[dict[str, Any]]
    _raise: Exception | None = None

    async def cookies(self) -> list[dict[str, Any]]:
        if self._raise is not None:
            raise self._raise
        return list(self._cookies)


@dataclass
class _FakeVault:
    """Stand-in for ``Vault``. Tracks reads + writes for assertion."""

    _records: dict[str, SessionRecord]
    writes: list[tuple[str, bytes]] = field(default_factory=list)
    write_raises: Exception | None = None

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return self._records.get(session_id)

    async def update_session_state_blob(self, session_id: str, state_blob: bytes) -> None:
        if self.write_raises is not None:
            raise self.write_raises
        self.writes.append((session_id, state_blob))


def _make_session(
    *,
    session_id: str,
    origin: str,
    allowed_paths: list[str],
    context: _FakeContext,
) -> OpenSession:
    policy = Policy(origin=origin, allowed_paths=allowed_paths)
    engine = PolicyEngine(policy)
    return OpenSession(
        handle="h-test",
        session_id=session_id,
        agent_id="agent-test",
        purpose="test",
        origin=origin,
        cdp_url="ws://127.0.0.1:0/devtools/browser/x",
        opened_at=0,
        expires_at=0,
        context=context,  # type: ignore[arg-type]  # fake duck-type
        user_data_dir=Path("/tmp/unused"),
        engine=engine,
    )


def _make_server(vault: _FakeVault) -> SessionServer:
    # Bypass __init__ so we don't have to construct a real Vault.
    server = SessionServer.__new__(SessionServer)
    server._vault = vault  # type: ignore[attr-defined]
    return server


def _make_record(*, session_id: str, origin: str, cookies: list[dict[str, Any]]) -> SessionRecord:
    blob = compress_blob({"cookies": cookies, "localStorage": {}, "sessionStorage": {}})
    return SessionRecord(
        id=session_id,
        origin=origin,
        created_at=0,
        status="active",
        state_blob=blob,
    )


@pytest.mark.asyncio
async def test_writeback_persists_added_cookie() -> None:
    original = [
        {"name": "s", "domain": ".github.com", "path": "/", "value": "v0"},
    ]
    live = [
        {"name": "s", "domain": ".github.com", "path": "/", "value": "v0"},
        {"name": "csrf", "domain": ".github.com", "path": "/", "value": "fresh"},
    ]
    vault = _FakeVault(
        {"s1": _make_record(session_id="s1", origin="https://github.com", cookies=original)}
    )
    server = _make_server(vault)
    session = _make_session(
        session_id="s1",
        origin="https://github.com",
        allowed_paths=["/issues/**"],
        context=_FakeContext(live),
    )

    counts = await server._writeback_state(session=session, reason="agent_closed")
    assert counts == {"unchanged": 1, "updated": 0, "added": 1, "dropped_by_policy": 0}
    assert len(vault.writes) == 1
    persisted = decompress_blob(vault.writes[0][1])
    names = {c["name"] for c in persisted["cookies"]}
    assert names == {"s", "csrf"}


@pytest.mark.asyncio
async def test_writeback_persists_updated_cookie_value() -> None:
    original = [
        {"name": "s", "domain": ".x.com", "path": "/", "value": "v0"},
    ]
    live = [
        {"name": "s", "domain": ".x.com", "path": "/", "value": "v1-rotated"},
    ]
    vault = _FakeVault(
        {"s1": _make_record(session_id="s1", origin="https://x.com", cookies=original)}
    )
    server = _make_server(vault)
    session = _make_session(
        session_id="s1",
        origin="https://x.com",
        allowed_paths=["/feed/**"],
        context=_FakeContext(live),
    )

    counts = await server._writeback_state(session=session, reason="agent_closed")
    assert counts is not None
    assert counts["updated"] == 1
    persisted = decompress_blob(vault.writes[0][1])
    assert persisted["cookies"][0]["value"] == "v1-rotated"


@pytest.mark.asyncio
async def test_writeback_drops_cookie_outside_policy_path() -> None:
    original = [
        {"name": "s", "domain": ".x.com", "path": "/api", "value": "v0"},
    ]
    live = [
        # Cookie at /admin must not be persisted because /admin doesn't
        # overlap allowed_paths ["/api/**"].
        {"name": "evil", "domain": ".x.com", "path": "/admin", "value": "set-by-agent"},
        {"name": "s", "domain": ".x.com", "path": "/api", "value": "v0-bumped"},
    ]
    vault = _FakeVault(
        {"s1": _make_record(session_id="s1", origin="https://x.com", cookies=original)}
    )
    server = _make_server(vault)
    session = _make_session(
        session_id="s1",
        origin="https://x.com",
        allowed_paths=["/api/**"],
        context=_FakeContext(live),
    )

    counts = await server._writeback_state(session=session, reason="agent_closed")
    assert counts is not None
    assert counts["dropped_by_policy"] == 1
    persisted = decompress_blob(vault.writes[0][1])
    names = {c["name"] for c in persisted["cookies"]}
    assert "evil" not in names  # blocked
    assert "s" in names  # legitimate update preserved


@pytest.mark.asyncio
async def test_writeback_preserves_original_cookies_not_seen_in_live() -> None:
    original = [
        {"name": "s", "domain": ".x.com", "path": "/", "value": "v0"},
        {"name": "pref", "domain": ".x.com", "path": "/", "value": "dark"},
    ]
    live = [
        # Only `s` came back from the live jar; `pref` is missing (maybe
        # expired, maybe agent cleared it). We must preserve `pref`.
        {"name": "s", "domain": ".x.com", "path": "/", "value": "v1"},
    ]
    vault = _FakeVault(
        {"s1": _make_record(session_id="s1", origin="https://x.com", cookies=original)}
    )
    server = _make_server(vault)
    session = _make_session(
        session_id="s1",
        origin="https://x.com",
        allowed_paths=["/feed/**"],
        context=_FakeContext(live),
    )

    counts = await server._writeback_state(session=session, reason="agent_closed")
    assert counts is not None
    persisted_names = {c["name"] for c in decompress_blob(vault.writes[0][1])["cookies"]}
    assert persisted_names == {"s", "pref"}


@pytest.mark.asyncio
async def test_writeback_skipped_on_revoke() -> None:
    vault = _FakeVault({"s1": _make_record(session_id="s1", origin="https://x.com", cookies=[])})
    server = _make_server(vault)
    session = _make_session(
        session_id="s1",
        origin="https://x.com",
        allowed_paths=["/**"],
        context=_FakeContext([{"name": "x", "domain": ".x.com", "path": "/", "value": "v"}]),
    )

    counts = await server._writeback_state(session=session, reason="session_revoked")
    assert counts is None
    assert vault.writes == []


@pytest.mark.asyncio
async def test_writeback_skipped_on_shutdown() -> None:
    vault = _FakeVault({"s1": _make_record(session_id="s1", origin="https://x.com", cookies=[])})
    server = _make_server(vault)
    session = _make_session(
        session_id="s1",
        origin="https://x.com",
        allowed_paths=["/**"],
        context=_FakeContext([{"name": "x", "domain": ".x.com", "path": "/", "value": "v"}]),
    )

    counts = await server._writeback_state(session=session, reason="daemon_shutdown")
    assert counts is None
    assert vault.writes == []


@pytest.mark.asyncio
async def test_writeback_skipped_when_policy_has_no_allowed_paths() -> None:
    vault = _FakeVault({"s1": _make_record(session_id="s1", origin="https://x.com", cookies=[])})
    server = _make_server(vault)
    session = _make_session(
        session_id="s1",
        origin="https://x.com",
        allowed_paths=[],
        context=_FakeContext([{"name": "x", "domain": ".x.com", "path": "/", "value": "v"}]),
    )

    counts = await server._writeback_state(session=session, reason="agent_closed")
    assert counts is None
    assert vault.writes == []


@pytest.mark.asyncio
async def test_writeback_returns_none_when_nothing_changed() -> None:
    original = [{"name": "s", "domain": ".x.com", "path": "/", "value": "v0"}]
    live = [{"name": "s", "domain": ".x.com", "path": "/", "value": "v0"}]
    vault = _FakeVault(
        {"s1": _make_record(session_id="s1", origin="https://x.com", cookies=original)}
    )
    server = _make_server(vault)
    session = _make_session(
        session_id="s1",
        origin="https://x.com",
        allowed_paths=["/**"],
        context=_FakeContext(live),
    )

    counts = await server._writeback_state(session=session, reason="agent_closed")
    # All cookies are unchanged: skip the vault write entirely.
    assert counts is None
    assert vault.writes == []


@pytest.mark.asyncio
async def test_writeback_swallows_cookies_failure() -> None:
    vault = _FakeVault({"s1": _make_record(session_id="s1", origin="https://x.com", cookies=[])})
    server = _make_server(vault)
    session = _make_session(
        session_id="s1",
        origin="https://x.com",
        allowed_paths=["/**"],
        context=_FakeContext([], _raise=RuntimeError("context already closed")),
    )

    counts = await server._writeback_state(session=session, reason="agent_closed")
    assert counts is None
    assert vault.writes == []


@pytest.mark.asyncio
async def test_writeback_skipped_when_vault_record_missing() -> None:
    vault = _FakeVault({})  # no record for session_id
    server = _make_server(vault)
    session = _make_session(
        session_id="missing",
        origin="https://x.com",
        allowed_paths=["/**"],
        context=_FakeContext([]),
    )

    counts = await server._writeback_state(session=session, reason="agent_closed")
    assert counts is None
    assert vault.writes == []
