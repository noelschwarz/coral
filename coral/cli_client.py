"""HTTP-API client helpers used by the ``coral`` CLI.

Extracted from ``coral.cli`` so the request/response plumbing is unit-testable
without spinning up a Typer subprocess. The helpers are deliberately tiny and
synchronous — they wrap ``urllib.request`` and never reach for a third-party
HTTP client, so they work in any Python install without extra deps.

Audit discipline: bearer tokens flow through these helpers as positional
arguments. Never log them. Never include them in error messages.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Literal, cast

Method = Literal["GET", "POST", "PUT", "DELETE"]


def read_cli_token(coral_dir: Path) -> str | None:
    """Read the daemon's bridge token from ``$CORAL_HOME/cli.token``.

    Returns ``None`` if the file is missing, empty, or unreadable. Never raises.
    """
    token_path = coral_dir / "cli.token"
    if not token_path.is_file():
        return None
    try:
        return token_path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def as_list_of_dicts(value: Any) -> list[dict[str, Any]] | None:
    """Coerce a parsed-JSON value into ``list[dict[str, Any]]`` or return ``None``."""
    if not isinstance(value, list):
        return None
    out: list[dict[str, Any]] = []
    for item in value:  # pyright: ignore[reportUnknownVariableType]
        if isinstance(item, dict):
            out.append(item)  # pyright: ignore[reportUnknownArgumentType]
    return out


def _parse_body(body: bytes) -> dict[str, Any]:
    """JSON-decode a response body into a dict, or return ``{}`` on shape mismatch."""
    if not body:
        return {}
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return cast(dict[str, Any], decoded) if isinstance(decoded, dict) else {}


def http_request(
    method: Method,
    url: str,
    *,
    token: str,
    body: dict[str, Any] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, Any]]:
    """Issue an authenticated HTTP request and return ``(status, parsed_body)``.

    - Network errors (URLError, timeout) collapse to ``status=0`` so callers
      can distinguish "no response" from any real HTTP status.
    - 4xx/5xx responses still return their body if it's JSON.
    - Bearer token is sent via the standard ``Authorization`` header. The
      caller is responsible for not logging it; this function never does.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, _parse_body(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, _parse_body(e.read())
    except (urllib.error.URLError, TimeoutError):
        return 0, {}
