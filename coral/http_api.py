"""FastAPI HTTP surface for extension + CLI integration (spec §5.1).

The daemon listens on ``127.0.0.1:8765`` by default. Authenticated routes are
introduced in week 1; this module currently exposes liveness only.
"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from coral import __version__

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe (no auth)."""
    return {"status": "ok", "version": __version__}


def build_http_app() -> FastAPI:
    """Build the daemon HTTP API application (extension/CLI integration)."""
    app = FastAPI(title="Coral Daemon", version=__version__)
    app.include_router(router)
    return app
