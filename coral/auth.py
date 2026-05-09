"""Bearer-token authentication for the daemon HTTP API (spec §5.1, §6.2 T2).

Tokens are minted at ``POST /auth/handshake`` and stored as SHA-256 hashes in
``api_tokens``. The middleware never logs the submitted token nor its hash; failure
audit rows record only the reason.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status

from coral import diag
from coral.crypto import hash_token
from coral.models import AuditEntry
from coral.vault import Vault


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Identity attached to an authenticated request."""

    token_hash: str
    name: str
    authenticated_at: float


def get_vault(request: Request) -> Vault:
    """Return the daemon-owned vault attached to the FastAPI app state."""
    vault = getattr(request.app.state, "vault", None)
    if vault is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="vault_not_ready",
        )
    return vault


async def _record_auth_failure(vault: Vault, reason: str) -> None:
    diag.warn("auth.rejected", reason=reason, transport="http")
    entry = AuditEntry(
        timestamp=int(time.time()),
        session_id=None,
        agent_id=None,
        event_type="auth.failed",
        origin=None,
        detail=json.dumps({"reason": reason}, separators=(",", ":")),
    )
    await vault.insert_audit(entry)


async def require_auth(
    request: Request,
    vault: Vault = Depends(get_vault),
) -> AuthContext:
    """FastAPI dependency that enforces ``Authorization: Bearer <token>``.

    On failure the audit row records *why* (token not found, expired, malformed)
    but never the token itself or its hash.
    """
    header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing_authorization",
            headers={"WWW-Authenticate": 'Bearer realm="coral"'},
        )

    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_authorization_scheme",
            headers={"WWW-Authenticate": 'Bearer realm="coral"'},
        )

    token = parts[1].strip()
    digest = hash_token(token)

    record = await vault.verify_token(token)
    if record is None:
        await _record_auth_failure(vault, reason="token_not_found")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_token",
            headers={"WWW-Authenticate": 'Bearer realm="coral"'},
        )

    now = int(time.time())
    if record.expires_at < now:
        await _record_auth_failure(vault, reason="token_expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token_expired",
            headers={"WWW-Authenticate": 'Bearer realm="coral"'},
        )

    await vault.touch_token_last_used(digest, now)
    return AuthContext(token_hash=digest, name=record.name, authenticated_at=float(now))
