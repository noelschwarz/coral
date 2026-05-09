"""Tests for the cookie + storage restoration helpers."""

from __future__ import annotations

import json

from coral.restoration import (
    cookies_to_playwright,
    storage_init_script,
)


def test_cookie_minimal_round_trip() -> None:
    blob = {
        "cookies": [
            {"name": "sid", "value": "abc", "domain": ".example.com"},
        ]
    }
    out = cookies_to_playwright(blob)
    assert out == [
        {"name": "sid", "value": "abc", "domain": ".example.com", "path": "/"},
    ]


def test_cookie_full_attributes_passed_through() -> None:
    blob = {
        "cookies": [
            {
                "name": "li_at",
                "value": "x",
                "domain": ".linkedin.com",
                "path": "/feed",
                "expires": 1730000000.5,
                "httpOnly": True,
                "secure": True,
                "sameSite": "None",
            }
        ]
    }
    out = cookies_to_playwright(blob)
    assert len(out) == 1
    c = out[0]
    assert c["expires"] == 1730000000.5
    assert c["httpOnly"] is True
    assert c["secure"] is True
    assert c["sameSite"] == "None"
    assert c["path"] == "/feed"


def test_cookie_drops_invalid_entries() -> None:
    blob = {
        "cookies": [
            {"name": "ok", "value": "v", "domain": ".x.com"},
            {"name": 123, "value": "v", "domain": ".x.com"},  # bad name
            {"value": "v", "domain": ".x.com"},  # missing name
            "not-a-dict",
        ]
    }
    out = cookies_to_playwright(blob)
    assert [c["name"] for c in out] == ["ok"]


def test_cookie_normalizes_same_site_casing() -> None:
    blob = {
        "cookies": [
            {"name": "a", "value": "v", "domain": ".x.com", "sameSite": "lax"},
            {"name": "b", "value": "v", "domain": ".x.com", "same_site": "STRICT"},
            {"name": "c", "value": "v", "domain": ".x.com", "sameSite": "weird"},
        ]
    }
    out = {c["name"]: c for c in cookies_to_playwright(blob)}
    assert out["a"]["sameSite"] == "Lax"
    assert out["b"]["sameSite"] == "Strict"
    assert "sameSite" not in out["c"]


def test_storage_init_script_returns_none_when_empty() -> None:
    assert storage_init_script({"local_storage": {}, "session_storage": {}}) is None
    assert storage_init_script({}) is None


def test_storage_init_script_embeds_payload() -> None:
    blob = {
        "local_storage": {"theme": "dark", "n": 3},
        "session_storage": {"flash": "ok"},
    }
    script = storage_init_script(blob)
    assert script is not None
    # the payload must be JSON-serialized inline
    expected_local = json.dumps({"theme": "dark", "n": "3"}, separators=(",", ":"))
    assert "theme" in script
    assert "dark" in script
    assert "flash" in script
    assert "ok" in script
    # values are always coerced to strings
    assert "n" in script and '"3"' in expected_local


def test_storage_init_script_skips_none_values() -> None:
    blob = {"local_storage": {"keep": "v", "drop": None}}
    script = storage_init_script(blob)
    assert script is not None
    assert '"keep":"v"' in script
    assert "drop" not in script


def test_cookies_missing_or_wrong_type() -> None:
    assert cookies_to_playwright({}) == []
    assert cookies_to_playwright({"cookies": "not-a-list"}) == []
    assert cookies_to_playwright({"cookies": None}) == []
