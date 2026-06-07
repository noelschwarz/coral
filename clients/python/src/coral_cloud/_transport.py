"""HTTP transport abstraction.

The :class:`Transport` protocol lets the client talk to a real server in
production and to an in-process fake in tests, without the client knowing
the difference. The default implementation wraps ``httpx``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ._version import __version__


@dataclass
class Response:
    """A minimal transport-agnostic HTTP response."""

    status_code: int
    data: Any = None  # parsed JSON body (dict / list / None)


@runtime_checkable
class Transport(Protocol):
    """Anything that can perform a JSON request/response round-trip."""

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Response: ...

    def close(self) -> None: ...


class HttpxTransport:
    """Default transport: a thin wrapper over ``httpx.Client``."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float = 30.0,
    ) -> None:
        import httpx  # imported lazily so tests with a fake transport need no httpx

        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": f"coral-cloud-python/{__version__}",
                "Accept": "application/json",
            },
            timeout=timeout,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Response:
        resp = self._client.request(method, path, params=params, json=json)
        data: Any = None
        if resp.content:
            try:
                data = resp.json()
            except ValueError:
                data = None
        return Response(status_code=resp.status_code, data=data)

    def close(self) -> None:
        self._client.close()
