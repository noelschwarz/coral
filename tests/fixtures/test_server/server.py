"""Local FastAPI fixture used by Coral integration and end-to-end tests.

Real third-party sites (LinkedIn, Gmail, etc.) cannot be hit in CI per the
engineering spec §8.3. This fixture stands in: ``/login`` issues a session
cookie; ``/me`` echoes user data when the cookie is present; ``/protected``
requires the cookie. The session-restoration tests POST a captured cookie
into the test server's ``/me`` to assert authenticated access.
"""

from __future__ import annotations

from fastapi import Cookie, FastAPI, HTTPException, Response

DEMO_USER = "demo@coral.test"
COOKIE_NAME = "demo_session"
COOKIE_VALUE = "demo-session-value-abc123"


def build_app() -> FastAPI:
    app = FastAPI(title="Coral test server")

    @app.get("/")
    def root() -> dict[str, str]:
        return {"server": "coral-test-server"}

    @app.post("/login")
    def login(response: Response) -> dict[str, str]:
        response.set_cookie(
            key=COOKIE_NAME,
            value=COOKIE_VALUE,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return {"logged_in_as": DEMO_USER}

    @app.get("/me")
    def me(demo_session: str | None = Cookie(default=None)) -> dict[str, str]:
        if demo_session != COOKIE_VALUE:
            raise HTTPException(status_code=401, detail="not_authenticated")
        return {"user": DEMO_USER, "cookie_seen": demo_session}

    @app.get("/protected")
    def protected(demo_session: str | None = Cookie(default=None)) -> dict[str, str]:
        if demo_session != COOKIE_VALUE:
            raise HTTPException(status_code=401, detail="not_authenticated")
        return {"ok": "true"}

    return app
