"""Pydantic models for vault rows (engineering spec §4.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SessionStatus = Literal["active", "expired", "revoked"]

SPEC_SCHEMA_TABLES: tuple[str, ...] = ("sessions", "policies", "audit_log", "api_tokens")


class SessionRecord(BaseModel):
    """Row in ``sessions``."""

    id: str
    origin: str
    label: str | None = None
    created_at: int
    last_used_at: int | None = None
    expires_at: int | None = None
    status: SessionStatus
    state_blob: bytes
    metadata: str = Field(default="{}")


class TokenRecord(BaseModel):
    """Row in ``api_tokens`` (token stored only as hash at rest)."""

    token_hash: str
    name: str
    created_at: int
    last_used_at: int | None = None
    expires_at: int


class AuditEntry(BaseModel):
    """Append-only ``audit_log`` row."""

    id: int | None = None
    timestamp: int
    session_id: str | None = None
    agent_id: str | None = None
    event_type: str
    origin: str | None = None
    detail: str


class PolicyRecord(BaseModel):
    """Row in ``policies``."""

    origin: str
    yaml_body: str
    updated_at: int


def schema_table_names() -> list[str]:
    """Stable ordered table names from §4.1 (excluding internal vault tables)."""
    return list(SPEC_SCHEMA_TABLES)
