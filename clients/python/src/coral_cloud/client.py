"""The :class:`CoralCloud` client — the drop-in entry point.

Typical use::

    from coral_cloud import CoralCloud

    coral = CoralCloud()  # reads CORAL_API_KEY from the environment

    # One-time: capture a session by logging in through a hosted browser.
    login = coral.start_login("https://github.com")
    print("Open this and log in:", login.live_view_url)
    login = coral.wait_for_login(login.login_id)

    # Anytime after: hand an agent an authenticated browser.
    with coral.session(login.session_id, purpose="triage issues") as s:
        # s.cdp_url is a wss:// endpoint — drive it with any CDP client.
        ...

**Status: design-stage.** No server implements the protocol yet
(``protocol/openapi.yaml``); this skeleton is exercised against an
in-process fake in the test suite.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import quote

from ._transport import HttpxTransport, Response, Transport
from .exceptions import CoralConfigError, error_for_status
from .models import (
    AuditEntry,
    CloseResult,
    LoginSession,
    OpenSession,
    Policy,
    Session,
)

DEFAULT_BASE_URL = "https://api.coral.dev"
_API_KEY_ENV = "CORAL_API_KEY"
_BASE_URL_ENV = "CORAL_BASE_URL"


class CoralCloud:
    """Client for the Coral Cloud API.

    Parameters
    ----------
    api_key:
        Project API key (``ck_live_…``). Falls back to ``$CORAL_API_KEY``.
    base_url:
        API base URL. Falls back to ``$CORAL_BASE_URL`` then the default.
    timeout:
        Per-request timeout in seconds (default transport only).
    transport:
        Inject a custom :class:`~coral_cloud._transport.Transport` (used in
        tests). When provided, ``api_key`` may be omitted.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 30.0,
        transport: Transport | None = None,
    ) -> None:
        self._base_url = base_url or os.environ.get(_BASE_URL_ENV) or DEFAULT_BASE_URL
        if transport is not None:
            self._transport: Transport = transport
        else:
            key = api_key or os.environ.get(_API_KEY_ENV)
            if not key:
                raise CoralConfigError(
                    "No API key provided. Pass api_key=... or set the "
                    f"{_API_KEY_ENV} environment variable."
                )
            self._transport = HttpxTransport(base_url=self._base_url, api_key=key, timeout=timeout)

    # -- low-level ---------------------------------------------------------

    def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        resp: Response = self._transport.request(method, path, params=params, json=json)
        if resp.status_code >= 400:
            code, message = None, None
            if isinstance(resp.data, dict):
                code = resp.data.get("error")
                message = resp.data.get("message")
            raise error_for_status(resp.status_code, code=code, message=message)
        return resp.data

    # -- meta --------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Liveness probe; does not require auth on the server side."""
        return self._call("GET", "/v1/health") or {}

    # -- sessions ----------------------------------------------------------

    def list_sessions(
        self,
        *,
        origin: str | None = None,
        status: str | None = None,
    ) -> list[Session]:
        """List captured sessions for the authenticated project."""
        params: dict[str, Any] = {}
        if origin is not None:
            params["origin"] = origin
        if status is not None:
            params["status"] = status
        data = self._call("GET", "/v1/sessions", params=params or None)
        rows = (data or {}).get("sessions", [])
        return [Session.model_validate(r) for r in rows]

    def open_session(self, session_id: str, *, purpose: str) -> OpenSession:
        """Open an isolated cloud browser restored from a captured session.

        Returns an :class:`OpenSession` whose ``cdp_url`` is a ``wss://``
        endpoint. Remember to :meth:`close_session` when done, or use the
        :meth:`session` context manager which closes automatically.
        """
        data = self._call(
            "POST",
            f"/v1/sessions/{quote(session_id, safe='')}/open",
            json={"purpose": purpose},
        )
        return OpenSession.model_validate(data)

    def close_session(self, handle: str) -> CloseResult:
        """Close an open session and tear down its browser. Idempotent."""
        data = self._call("POST", f"/v1/sessions/handles/{quote(handle, safe='')}/close")
        return CloseResult.model_validate(data or {"closed": True})

    @contextmanager
    def session(self, session_id: str, *, purpose: str) -> Iterator[OpenSession]:
        """Context manager: open a session and guarantee it is closed.

        ::

            with coral.session(sid, purpose="...") as s:
                drive(s.cdp_url)
        """
        opened = self.open_session(session_id, purpose=purpose)
        try:
            yield opened
        finally:
            self.close_session(opened.handle)

    # -- remote login (capture) -------------------------------------------

    def start_login(self, origin: str, *, label: str | None = None) -> LoginSession:
        """Start a remote interactive login for ``origin``.

        Hand the returned ``live_view_url`` to the user; they log in
        (2FA/captcha handled in-browser), then poll with :meth:`get_login`
        or block with :meth:`wait_for_login`.
        """
        body: dict[str, Any] = {"origin": origin}
        if label is not None:
            body["label"] = label
        data = self._call("POST", "/v1/logins", json=body)
        return LoginSession.model_validate(data)

    def get_login(self, login_id: str) -> LoginSession:
        """Fetch the current state of a remote login."""
        data = self._call("GET", f"/v1/logins/{quote(login_id, safe='')}")
        return LoginSession.model_validate(data)

    def wait_for_login(
        self,
        login_id: str,
        *,
        timeout: float = 300.0,
        poll_interval: float = 2.0,
    ) -> LoginSession:
        """Poll a login until it reaches a terminal state or ``timeout``.

        Returns the final :class:`LoginSession` (which may be ``captured``,
        ``expired``, or ``failed``). Raises :class:`TimeoutError` if the
        deadline passes first.
        """
        deadline = time.monotonic() + timeout
        while True:
            login = self.get_login(login_id)
            if login.is_terminal:
                return login
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"login {login_id} did not complete within {timeout}s "
                    f"(last status: {login.status})"
                )
            time.sleep(poll_interval)

    # -- policies ----------------------------------------------------------

    def list_policies(self) -> list[Policy]:
        """List the project's policies.

        On first access the project is seeded with the bundled behavior
        packs, so this returns those defaults until you override them.
        """
        data = self._call("GET", "/v1/policies")
        rows = (data or {}).get("policies", [])
        return [Policy.model_validate(r) for r in rows]

    def get_policy(self, origin: str) -> Policy:
        data = self._call("GET", f"/v1/policies/{quote(origin, safe='')}")
        return Policy.model_validate(data)

    def put_policy(self, origin: str, yaml_body: str) -> Policy:
        data = self._call(
            "PUT",
            f"/v1/policies/{quote(origin, safe='')}",
            json={"yaml_body": yaml_body},
        )
        return Policy.model_validate(data)

    # -- audit -------------------------------------------------------------

    def get_audit(self, *, since: int = 0, limit: int = 50) -> list[AuditEntry]:
        data = self._call("GET", "/v1/audit", params={"since": since, "limit": limit})
        rows = (data or {}).get("entries", [])
        return [AuditEntry.model_validate(r) for r in rows]

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Release the underlying transport (HTTP connection pool)."""
        self._transport.close()

    def __enter__(self) -> CoralCloud:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
