"""Unit tests for the CLI's HTTP-client helpers (Track F cleanup)."""

from __future__ import annotations

from coral.cli_client import as_list_of_dicts, http_request, read_cli_token

# read_cli_token ------------------------------------------------------------


def test_read_cli_token_missing_dir(tmp_path) -> None:
    assert read_cli_token(tmp_path) is None


def test_read_cli_token_present(tmp_path) -> None:
    (tmp_path / "cli.token").write_text("abc123\n", encoding="utf-8")
    assert read_cli_token(tmp_path) == "abc123"


def test_read_cli_token_empty(tmp_path) -> None:
    (tmp_path / "cli.token").write_text("   \n", encoding="utf-8")
    assert read_cli_token(tmp_path) is None


# as_list_of_dicts ----------------------------------------------------------


def test_as_list_of_dicts_happy_path() -> None:
    assert as_list_of_dicts([{"a": 1}, {"b": 2}]) == [{"a": 1}, {"b": 2}]


def test_as_list_of_dicts_filters_non_dict_items() -> None:
    assert as_list_of_dicts([{"a": 1}, "string", 3, None]) == [{"a": 1}]


def test_as_list_of_dicts_rejects_non_list() -> None:
    assert as_list_of_dicts({"not": "a list"}) is None
    assert as_list_of_dicts(None) is None
    assert as_list_of_dicts(42) is None


# http_request --------------------------------------------------------------
#
# We don't spin up a real server here — those paths are exercised end-to-end by
# the integration suite. These tests cover the "no network" branch (status=0)
# and assert the request shape via a captured Request object.


def test_http_request_url_error_returns_zero() -> None:
    # An unreachable port returns (0, {}) per the contract.
    code, body = http_request("GET", "http://127.0.0.1:1/never-listening", token="t", timeout=0.5)
    assert code == 0
    assert body == {}


def test_http_request_builds_authorization_and_body(monkeypatch) -> None:
    """Verify the request shape without sending it over the wire."""
    import json as _json
    import urllib.request

    captured: dict[str, object] = {}

    class _FakeResp:
        status = 204

        def __init__(self) -> None:
            self._body = b""

        def read(self) -> bytes:
            return self._body

        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(req, timeout: float = 0.0):  # type: ignore[no-untyped-def]
        captured["method"] = req.get_method()
        captured["headers"] = dict(req.header_items())
        captured["data"] = req.data
        captured["url"] = req.full_url
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    code, body = http_request(
        "POST",
        "http://127.0.0.1:8765/auth/refresh",
        token="secret-token",
        body={"client_name": "extension"},
    )
    assert code == 204
    assert body == {}
    assert captured["method"] == "POST"
    assert captured["url"] == "http://127.0.0.1:8765/auth/refresh"
    raw_data = captured["data"]
    assert isinstance(raw_data, bytes)
    assert _json.loads(raw_data.decode("utf-8")) == {"client_name": "extension"}
    headers: dict[str, str] = {
        k.lower(): str(v)
        for k, v in (captured["headers"] or {}).items()  # type: ignore[union-attr]
    }
    assert headers.get("authorization") == "Bearer secret-token"
    assert headers.get("content-type") == "application/json"


def test_http_request_no_body_omits_content_type(monkeypatch) -> None:
    import urllib.request

    captured: dict[str, object] = {}

    class _FakeResp:
        status = 200

        def read(self) -> bytes:
            return b'{"ok":true}'

        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def fake_urlopen(req, timeout: float = 0.0):  # type: ignore[no-untyped-def]
        captured["headers"] = dict(req.header_items())
        captured["data"] = req.data
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    code, body = http_request("GET", "http://x/y", token="t")
    assert code == 200
    assert body == {"ok": True}
    assert captured["data"] is None
    headers: dict[str, str] = {
        k.lower(): str(v)
        for k, v in (captured["headers"] or {}).items()  # type: ignore[union-attr]
    }
    assert "content-type" not in headers


def test_http_request_handles_4xx_body(monkeypatch) -> None:
    """HTTPError responses still return a parsed JSON body when available."""
    import urllib.error
    import urllib.request

    def fake_urlopen(req, timeout: float = 0.0):  # type: ignore[no-untyped-def]
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=404,
            msg="not found",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )

    # HTTPError needs `.read()` for our parser. Patch its read to return JSON.
    class _PatchedHTTPError(urllib.error.HTTPError):
        def read(self) -> bytes:  # type: ignore[override]
            return b'{"error":"not_found"}'

    def fake_raise(req, timeout: float = 0.0):  # type: ignore[no-untyped-def]
        raise _PatchedHTTPError(
            url=req.full_url,
            code=404,
            msg="not found",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_raise)
    code, body = http_request("DELETE", "http://x/y/z", token="t")
    assert code == 404
    assert body == {"error": "not_found"}
