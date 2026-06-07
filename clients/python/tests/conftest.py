"""Shared test fixtures: an in-process fake transport.

The fake records requests and replays scripted responses, so the SDK can
be exercised end-to-end without a running server (none exists yet) and
without httpx.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from coral_cloud._transport import Response


@dataclass
class RecordedRequest:
    method: str
    path: str
    params: dict[str, Any] | None
    json: dict[str, Any] | None


@dataclass
class FakeTransport:
    """A scripted :class:`~coral_cloud._transport.Transport`.

    Map ``(METHOD, path)`` to a :class:`Response`; unmatched calls return
    404 so tests fail loudly on unexpected routes.
    """

    routes: dict[tuple[str, str], Response] = field(default_factory=dict)
    requests: list[RecordedRequest] = field(default_factory=list)
    closed: bool = False

    def on(self, method: str, path: str, status_code: int, data: Any) -> FakeTransport:
        self.routes[(method.upper(), path)] = Response(status_code, data)
        return self

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Response:
        self.requests.append(RecordedRequest(method.upper(), path, params, json))
        return self.routes.get(
            (method.upper(), path),
            Response(404, {"error": "not_found", "message": f"no fake route for {method} {path}"}),
        )

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake() -> FakeTransport:
    return FakeTransport()
