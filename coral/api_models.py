"""Request and response models for the daemon HTTP API (spec §5.1).

Response models intentionally exclude ``state_blob`` — captured cookies and storage
must never leave the daemon over HTTP. They are restored only into Playwright contexts
the daemon owns (week 2).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from coral.models import SessionStatus


class HandshakeRequest(BaseModel):
    challenge: str = Field(min_length=1, max_length=64)
    client_name: str = Field(min_length=1, max_length=64)


class HandshakeResponse(BaseModel):
    token: str
    expires_at: int


class TokenRefreshResponse(BaseModel):
    token: str
    expires_at: int
    previous_revoked: bool


class TokenInfo(BaseModel):
    token_hash: str
    name: str
    created_at: int
    last_used_at: int | None
    expires_at: int


class TokenListResponse(BaseModel):
    tokens: list[TokenInfo]


class CookieIn(BaseModel):
    """A cookie shape sufficient for week-1 capture (spec §4.2)."""

    model_config = ConfigDict(extra="allow")

    name: str
    value: str
    domain: str | None = None
    path: str | None = "/"
    expires: float | int | None = None
    http_only: bool | None = None
    secure: bool | None = None
    same_site: str | None = None


class StateBlob(BaseModel):
    """Subset of §4.2 captured by the extension."""

    model_config = ConfigDict(extra="allow")

    version: int = 1
    cookies: list[CookieIn] = Field(default_factory=lambda: [])
    local_storage: dict[str, Any] = Field(default_factory=lambda: {})
    session_storage: dict[str, Any] = Field(default_factory=lambda: {})


class CaptureSessionRequest(BaseModel):
    origin: str = Field(min_length=1)
    label: str | None = None
    state: StateBlob

    @field_validator("origin")
    @classmethod
    def _validate_origin(cls, value: str) -> str:
        from urllib.parse import urlparse

        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("origin must use http(s) scheme")
        if not parsed.netloc:
            raise ValueError("origin must include host")
        if parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
            raise ValueError("origin must not include path, query, or fragment")
        return f"{parsed.scheme}://{parsed.netloc}"


class CaptureSessionResponse(BaseModel):
    session_id: str
    status: SessionStatus
    expires_at: int | None


class SessionListItem(BaseModel):
    """Sessions returned by ``GET /sessions`` (no ``state_blob``)."""

    id: str
    origin: str
    label: str | None
    created_at: int
    last_used_at: int | None
    expires_at: int | None
    status: SessionStatus


class SessionListResponse(BaseModel):
    sessions: list[SessionListItem]


class PolicyResponse(BaseModel):
    origin: str
    yaml_body: str
    updated_at: int


class PolicyPutRequest(BaseModel):
    yaml_body: str = Field(min_length=1, max_length=64 * 1024)


class AuditEntryResponse(BaseModel):
    id: int | None
    timestamp: int
    session_id: str | None
    agent_id: str | None
    event_type: str
    origin: str | None
    detail: str


class AuditListResponse(BaseModel):
    entries: list[AuditEntryResponse]
