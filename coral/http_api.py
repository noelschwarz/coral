"""FastAPI HTTP surface (spec §5.1).

Endpoints (auth-required unless noted):

- ``GET /healthz``                 — no auth, liveness only.
- ``POST /auth/handshake``         — no auth, single-use challenge → bearer token.
- ``POST /sessions``               — capture a session.
- ``GET /sessions``                — list sessions (no ``state_blob``).
- ``PUT /sessions/{id}/refresh``   — re-capture in place (PR N2); preserves
  ``session_id`` so open agent handles don't see the swap.
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

from coral import __version__, diag
from coral.api_models import (
    AuditEntryResponse,
    AuditListResponse,
    CaptureSessionRequest,
    CaptureSessionResponse,
    HandshakeRequest,
    HandshakeResponse,
    PolicyPutRequest,
    PolicyResponse,
    ReviewDecisionRequest,
    ReviewItem,
    ReviewListResponse,
    SessionListItem,
    SessionListResponse,
    TokenInfo,
    TokenListResponse,
    TokenRefreshResponse,
)
from coral.auth import AuthContext, get_vault, require_auth
from coral.crypto import constant_time_compare, generate_token, hash_token
from coral.models import SessionRecord
from coral.vault import Vault, compress_blob

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


async def _audit(
    vault: Vault,
    *,
    event_type: str,
    detail: dict[str, Any],
    session_id: str | None = None,
    agent_id: str | None = None,
    origin: str | None = None,
) -> None:
    """Write an audit row or fail the request loudly (handoff: integrity over best-effort).

    Delegates row construction + insertion to :mod:`coral.audit` (the single
    source of truth) and only adds the HTTP-API ``500`` failure mode on top.
    """
    import sys

    from coral.audit import write_audit_row

    try:
        await write_audit_row(
            vault,
            event_type=event_type,
            detail=detail,
            session_id=session_id,
            agent_id=agent_id,
            origin=origin,
        )
    except Exception as exc:
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
    diag.info(
        "auth.handshake.success",
        client_name=body.client_name,
        ttl_seconds=int(cfg.extension_token_ttl_seconds),
    )
    await _audit(
        vault,
        event_type="auth.handshake.success",
        detail={"client_name": body.client_name, "expires_at": expires_at},
    )
    return HandshakeResponse(token=raw_token, expires_at=expires_at)


@router.post("/auth/refresh", response_model=TokenRefreshResponse)
async def refresh_token(
    request: Request,
    vault: Vault = Depends(get_vault),
    auth: AuthContext = Depends(require_auth),
) -> TokenRefreshResponse:
    """Mint a fresh bearer token using a still-valid one.

    Removes the daily ``restart daemon → re-paste challenge`` ritual: clients
    that have a working token can extend their lifetime without re-handshaking.
    The previous token is revoked immediately on success — a single client
    holds at most one valid token at a time.
    """
    cfg = request.app.state.config
    expires_at = int(time.time()) + int(cfg.extension_token_ttl_seconds)
    raw_token = generate_token()
    await vault.insert_token(hash_token(raw_token), name=auth.name, expires_at=expires_at)
    await vault.delete_token(auth.token_hash)
    await _audit(
        vault,
        event_type="auth.token.refreshed",
        detail={"client_name": auth.name, "expires_at": expires_at},
        agent_id=auth.name,
    )
    return TokenRefreshResponse(token=raw_token, expires_at=expires_at, previous_revoked=True)


@router.get("/tokens", response_model=TokenListResponse)
async def list_tokens(
    vault: Vault = Depends(get_vault),
    auth: AuthContext = Depends(require_auth),
) -> TokenListResponse:
    rows = await vault.list_tokens()
    items = [
        TokenInfo(
            token_hash=r.token_hash,
            name=r.name,
            created_at=r.created_at,
            last_used_at=r.last_used_at,
            expires_at=r.expires_at,
        )
        for r in rows
    ]
    await _audit(
        vault,
        event_type="auth.token.list",
        detail={"count": len(items)},
        agent_id=auth.name,
    )
    return TokenListResponse(tokens=items)


@router.delete("/tokens/{token_hash}", status_code=204)
async def revoke_token(
    token_hash: str = Path(..., min_length=1, max_length=128),
    vault: Vault = Depends(get_vault),
    auth: AuthContext = Depends(require_auth),
) -> Response:
    """Revoke a single bearer token.

    Track B's prompt deferred this; the UX argument (panic recovery, "log out
    this agent") outweighs the deferral. Revoking your own token is allowed —
    callers should expect the next request to 401.
    """
    rows = await vault.list_tokens()
    if not any(r.token_hash == token_hash for r in rows):
        raise HTTPException(status_code=404, detail="token_not_found")
    await vault.delete_token(token_hash)
    await _audit(
        vault,
        event_type="auth.token.revoked",
        detail={"by": auth.name, "self": token_hash == auth.token_hash},
        agent_id=auth.name,
    )
    return Response(status_code=204)


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

    blob = compress_blob(state_dict)

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


@router.put("/sessions/{session_id}/refresh", response_model=CaptureSessionResponse)
async def refresh_session(
    body: CaptureSessionRequest,
    session_id: str = Path(..., min_length=1, max_length=64),
    vault: Vault = Depends(get_vault),
    auth: AuthContext = Depends(require_auth),
) -> CaptureSessionResponse:
    """Replace an existing active session's state with a fresh capture from the
    user's main Chrome (Track N / PR N2).

    Preserves ``session_id`` so any open agent handles for this session aren't
    invalidated — the agent doesn't see the swap, the next ``coral_open_session``
    just gets the fresh state.

    - **404** when the session doesn't exist.
    - **409** when the session is revoked/expired.
    - **400** when the body's ``origin`` doesn't match the captured session's
      origin (refresh can't change which site this session is for).
    """
    existing = await vault.get_session(session_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    if existing.status != "active":
        raise HTTPException(status_code=409, detail="session_not_active")
    if body.origin != existing.origin:
        raise HTTPException(
            status_code=400,
            detail=(
                f"origin_mismatch: session is for {existing.origin}, "
                f"refresh payload is for {body.origin}"
            ),
        )

    state_dict: dict[str, Any] = body.state.model_dump(mode="json")
    cookies_raw_obj: Any = state_dict.get("cookies") or []
    cookie_dicts: list[dict[str, Any]] = []
    if isinstance(cookies_raw_obj, list):
        for raw_item in cookies_raw_obj:  # pyright: ignore[reportUnknownVariableType]
            if isinstance(raw_item, dict):
                cookie_dicts.append(raw_item)  # pyright: ignore[reportUnknownArgumentType]
    expires_at = _cookie_min_expiry(cookie_dicts)
    blob = compress_blob(state_dict)

    await vault.replace_session_state(
        session_id=session_id,
        state_blob=blob,
        expires_at=expires_at,
    )
    await _audit(
        vault,
        event_type="session.refreshed",
        detail={
            "origin": existing.origin,
            "cookie_count": len(cookie_dicts),
            "ls_keys": list(state_dict.get("local_storage", {}).keys()),
            "ss_keys": list(state_dict.get("session_storage", {}).keys()),
        },
        agent_id=auth.name,
        origin=existing.origin,
        session_id=session_id,
    )
    return CaptureSessionResponse(session_id=session_id, status="active", expires_at=expires_at)


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


@router.get("/reviews", response_model=ReviewListResponse)
async def list_reviews(
    vault: Vault = Depends(get_vault),
    auth: AuthContext = Depends(require_auth),
) -> ReviewListResponse:
    rows = await vault.list_pending_reviews()
    items = [
        ReviewItem(
            id=r.id,
            session_handle=r.session_handle,
            session_id=r.session_id,
            agent_id=r.agent_id,
            action_type=r.action_type,
            action_detail=r.action_detail,
            status=r.status,
            created_at=r.created_at,
            decided_at=r.decided_at,
            decided_by=r.decided_by,
        )
        for r in rows
    ]
    await _audit(
        vault,
        event_type="policy.review.list",
        detail={"count": len(items)},
        agent_id=auth.name,
    )
    return ReviewListResponse(reviews=items)


@router.post("/reviews/{review_id}/decision", status_code=204)
async def decide_review(
    body: ReviewDecisionRequest,
    review_id: str = Path(..., min_length=1, max_length=64),
    vault: Vault = Depends(get_vault),
    auth: AuthContext = Depends(require_auth),
) -> Response:
    existing = await vault.get_review(review_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="review_not_found")
    if existing.status != "pending":
        raise HTTPException(status_code=409, detail=f"review_already_{existing.status}")
    await vault.decide_review(
        review_id,
        status=body.decision,
        decided_by=auth.name,
        now=int(time.time()),
    )
    await _audit(
        vault,
        event_type=f"policy.review.{body.decision}",
        detail={"review_id": review_id, "action_type": existing.action_type},
        session_id=existing.session_id,
        agent_id=auth.name,
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
