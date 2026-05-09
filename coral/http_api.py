"""FastAPI HTTP surface (spec §5.1).

Endpoints (auth-required unless noted):

- ``GET /healthz``                 — no auth, liveness only.
- ``POST /auth/handshake``         — no auth, single-use challenge → bearer token.
- ``POST /sessions``               — capture a session.
- ``GET /sessions``                — list sessions (no ``state_blob``).
- ``DELETE /sessions/{id}``        — revoke a session (zeroes ``state_blob``).
- ``GET /policies/{origin}``       — read per-origin YAML policy.
- ``PUT /policies/{origin}``       — upsert per-origin YAML policy.
- ``GET /audit``                   — query audit rows.

Non-functional posture:

- Bind address is ``127.0.0.1`` only (enforced by ``coral.daemon`` — there is no
  config path to change it).
- CORS allows ``chrome-extension://*`` and rejects everything else, including
  localhost web origins (spec §6.2 T4).
- Tokens never appear in logs or response bodies after handshake.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

import yaml
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from coral import __version__
from coral.api_models import (
    AuditEntryResponse,
    AuditListResponse,
    CaptureSessionRequest,
    CaptureSessionResponse,
    HandshakeRequest,
    HandshakeResponse,
    PolicyPutRequest,
    PolicyResponse,
    SessionListItem,
    SessionListResponse,
)
from coral.auth import AuthContext, get_vault, require_auth
from coral.crypto import constant_time_compare, generate_token, hash_token
from coral.models import AuditEntry, SessionRecord
from coral.vault import Vault

DEFAULT_AUDIT_LIMIT = 100
MAX_AUDIT_LIMIT = 1000
DEFAULT_AUDIT_LOOKBACK_SECONDS = 24 * 60 * 60

_CHROME_EXTENSION_ORIGIN = re.compile(r"^chrome-extension://[A-Za-z0-9_-]+/?$")


@dataclass
class HandshakeState:
    """Single-use challenge + per-process rate limit (spec §6.2 T3)."""

    challenge: str
    consumed: bool = False
    attempt_log: deque[float] = field(default_factory=lambda: deque(maxlen=64))
    rate_limit_per_minute: int = 5

    def record_attempt(self, now: float) -> bool:
        cutoff = now - 60.0
        while self.attempt_log and self.attempt_log[0] < cutoff:
            self.attempt_log.popleft()
        self.attempt_log.append(now)
        return len(self.attempt_log) <= self.rate_limit_per_minute


def _audit_detail(d: dict[str, Any]) -> str:
    return json.dumps(d, separators=(",", ":"), sort_keys=True)


async def _audit(
    vault: Vault,
    *,
    event_type: str,
    detail: dict[str, Any],
    session_id: str | None = None,
    agent_id: str | None = None,
    origin: str | None = None,
) -> None:
    """Write an audit row or fail the request loudly (handoff: integrity over best-effort)."""
    entry = AuditEntry(
        timestamp=int(time.time()),
        session_id=session_id,
        agent_id=agent_id,
        event_type=event_type,
        origin=origin,
        detail=_audit_detail(detail),
    )
    try:
        await vault.insert_audit(entry)
    except Exception as exc:
        import sys

        print(
            f"coral: audit log write failed ({event_type}): {exc!r}",
            file=sys.stderr,
            flush=True,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": "audit_log_write_failed",
                "detail": "Coral could not write to its audit log; "
                "this is a fatal integrity issue.",
            },
        ) from exc


def _cookie_min_expiry(cookies: list[dict[str, Any]]) -> int | None:
    expiries: list[int] = []
    for c in cookies:
        exp = c.get("expires")
        if exp is None:
            continue
        try:
            ev = int(float(exp))
        except (TypeError, ValueError):
            continue
        if ev > 0:
            expiries.append(ev)
    return min(expiries) if expiries else None


def _session_to_list_item(rec: SessionRecord) -> SessionListItem:
    return SessionListItem(
        id=rec.id,
        origin=rec.origin,
        label=rec.label,
        created_at=rec.created_at,
        last_used_at=rec.last_used_at,
        expires_at=rec.expires_at,
        status=rec.status,
    )


router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe (no auth)."""
    return {"status": "ok", "version": __version__}


@router.post("/auth/handshake", response_model=HandshakeResponse)
async def handshake(
    body: HandshakeRequest,
    request: Request,
    vault: Vault = Depends(get_vault),
) -> HandshakeResponse:
    state: HandshakeState | None = getattr(request.app.state, "handshake", None)
    if state is None:
        raise HTTPException(status_code=503, detail="handshake_not_ready")

    now = time.time()
    if not state.record_attempt(now):
        await _audit(
            vault,
            event_type="auth.handshake.rate_limited",
            detail={"attempts_in_window": len(state.attempt_log)},
        )
        raise HTTPException(status_code=429, detail="rate_limited")

    if state.consumed or not constant_time_compare(body.challenge, state.challenge):
        await _audit(
            vault,
            event_type="auth.handshake.failed",
            detail={"reason": "wrong_challenge", "client_name": body.client_name},
        )
        raise HTTPException(status_code=401, detail="invalid_challenge")

    state.consumed = True

    cfg = request.app.state.config
    expires_at = int(time.time()) + int(cfg.extension_token_ttl_seconds)
    raw_token = generate_token()
    await vault.insert_token(hash_token(raw_token), name=body.client_name, expires_at=expires_at)
    await _audit(
        vault,
        event_type="auth.handshake.success",
        detail={"client_name": body.client_name, "expires_at": expires_at},
    )
    return HandshakeResponse(token=raw_token, expires_at=expires_at)


@router.post("/sessions", response_model=CaptureSessionResponse)
async def capture_session(
    body: CaptureSessionRequest,
    vault: Vault = Depends(get_vault),
    auth: AuthContext = Depends(require_auth),
) -> CaptureSessionResponse:
    existing = await vault.list_sessions()
    if any(s.origin == body.origin and s.status == "active" for s in existing):
        raise HTTPException(
            status_code=409,
            detail="active_session_exists_for_origin",
        )

    state_dict: dict[str, Any] = body.state.model_dump(mode="json")
    cookies_raw_obj: Any = state_dict.get("cookies") or []
    cookie_dicts: list[dict[str, Any]] = []
    if isinstance(cookies_raw_obj, list):
        for raw_item in cookies_raw_obj:  # pyright: ignore[reportUnknownVariableType]
            if isinstance(raw_item, dict):
                cookie_dicts.append(raw_item)  # pyright: ignore[reportUnknownArgumentType]
    expires_at = _cookie_min_expiry(cookie_dicts)

    from coral.vault import _compress_blob

    blob = _compress_blob(state_dict)

    rec = SessionRecord(
        id=str(uuid.uuid4()),
        origin=body.origin,
        label=body.label,
        created_at=int(time.time()),
        last_used_at=None,
        expires_at=expires_at,
        status="active",
        state_blob=blob,
        metadata="{}",
    )
    await vault.insert_session(rec)
    await _audit(
        vault,
        event_type="session.captured",
        detail={
            "origin": body.origin,
            "label": body.label,
            "cookie_count": len(cookie_dicts),
            "ls_keys": list(state_dict.get("local_storage", {}).keys()),
            "ss_keys": list(state_dict.get("session_storage", {}).keys()),
        },
        agent_id=auth.name,
        origin=body.origin,
        session_id=rec.id,
    )
    return CaptureSessionResponse(session_id=rec.id, status="active", expires_at=expires_at)


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    vault: Vault = Depends(get_vault),
    auth: AuthContext = Depends(require_auth),
) -> SessionListResponse:
    rows = await vault.list_sessions()
    items = [_session_to_list_item(r) for r in rows]
    await _audit(
        vault,
        event_type="session.list",
        detail={"count": len(items)},
        agent_id=auth.name,
    )
    return SessionListResponse(sessions=items)


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke_session(
    session_id: str = Path(..., min_length=1, max_length=64),
    vault: Vault = Depends(get_vault),
    auth: AuthContext = Depends(require_auth),
) -> Response:
    existing = await vault.get_session(session_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    await vault.revoke_session(session_id)
    await _audit(
        vault,
        event_type="session.revoked",
        detail={"origin": existing.origin},
        session_id=session_id,
        agent_id=auth.name,
        origin=existing.origin,
    )
    return Response(status_code=204)


@router.get("/policies/{origin:path}", response_model=PolicyResponse)
async def get_policy(
    request: Request,
    origin: str = Path(..., min_length=1),
    vault: Vault = Depends(get_vault),
    auth: AuthContext = Depends(require_auth),
) -> PolicyResponse:
    decoded = unquote(origin)
    pol = await vault.get_policy(decoded)
    await _audit(
        vault,
        event_type="policy.read",
        detail={"origin": decoded, "exists": pol is not None},
        agent_id=auth.name,
        origin=decoded,
    )
    if pol is None:
        raise HTTPException(status_code=404, detail="policy_not_found")
    return PolicyResponse(origin=pol.origin, yaml_body=pol.yaml_body, updated_at=pol.updated_at)


@router.put("/policies/{origin:path}", status_code=204)
async def put_policy(
    body: PolicyPutRequest,
    origin: str = Path(..., min_length=1),
    vault: Vault = Depends(get_vault),
    auth: AuthContext = Depends(require_auth),
) -> Response:
    decoded = unquote(origin)
    try:
        yaml.safe_load(body.yaml_body)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=400, detail="invalid_yaml") from exc
    await vault.upsert_policy(decoded, body.yaml_body)
    await _audit(
        vault,
        event_type="policy.updated",
        detail={"origin": decoded, "yaml_length": len(body.yaml_body)},
        agent_id=auth.name,
        origin=decoded,
    )
    return Response(status_code=204)


@router.get("/audit", response_model=AuditListResponse)
async def get_audit(
    since: int | None = Query(default=None, ge=0),
    limit: int = Query(default=DEFAULT_AUDIT_LIMIT, ge=1, le=MAX_AUDIT_LIMIT),
    vault: Vault = Depends(get_vault),
    _auth: AuthContext = Depends(require_auth),
) -> AuditListResponse:
    if since is None:
        since = int(time.time()) - DEFAULT_AUDIT_LOOKBACK_SECONDS
    rows = await vault.query_audit(since=since, limit=limit)
    return AuditListResponse(
        entries=[
            AuditEntryResponse(
                id=r.id,
                timestamp=r.timestamp,
                session_id=r.session_id,
                agent_id=r.agent_id,
                event_type=r.event_type,
                origin=r.origin,
                detail=r.detail,
            )
            for r in rows
        ],
    )


def _build_app() -> FastAPI:
    app = FastAPI(title="Coral Daemon", version=__version__)
    app.include_router(router)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_origin_regex=r"^chrome-extension://[A-Za-z0-9_-]+$",
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )

    @app.exception_handler(HTTPException)
    async def _http_error_handler(  # pyright: ignore[reportUnusedFunction]
        _request: Request, exc: HTTPException
    ) -> Response:
        body: dict[str, Any] = (
            exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
        )
        return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers or {})

    return app


def build_http_app(
    *,
    vault: Vault | None = None,
    handshake_state: HandshakeState | None = None,
    config: Any | None = None,
) -> FastAPI:
    """Construct the daemon's FastAPI app and seed app state.

    ``vault``, ``handshake_state``, and ``config`` are required at runtime; left
    optional so existing tests that just want to hit ``/healthz`` keep working.
    """
    app = _build_app()
    app.state.vault = vault
    app.state.handshake = handshake_state
    app.state.config = config
    return app


# Backwards-compat: ``test_smoke.py`` and existing callers expect the no-arg form.
def build_http_app_default() -> FastAPI:
    return _build_app()


def is_chrome_extension_origin(origin: str) -> bool:
    """Public helper used by tests to verify the CORS allowlist."""
    return _CHROME_EXTENSION_ORIGIN.fullmatch(origin) is not None
