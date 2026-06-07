"""Typed response models for the Coral Cloud API.

These mirror the schemas in ``protocol/openapi.yaml``. They are
deliberately permissive (``extra="allow"``) so a server that adds fields
doesn't break older SDKs.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

SessionStatus = Literal["active", "expired", "revoked"]
LoginStatus = Literal["pending", "awaiting_user", "captured", "expired", "failed"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="allow")


class Session(_Model):
    """A captured session. Never carries ``state_blob`` (it stays server-side)."""

    id: str
    origin: str
    label: str | None = None
    status: SessionStatus
    created_at: int
    last_used_at: int | None = None
    expires_at: int | None = None
    attention_at: int | None = None
    attention_reason: str | None = None


class OpenSession(_Model):
    """An open cloud browser the agent can drive over CDP.

    ``cdp_url`` is a ``wss://`` endpoint — connect any Chrome DevTools
    Protocol client (Playwright ``connect_over_cdp``, Puppeteer, raw CDP).
    The shape mirrors the local product's ``coral_open_session`` response,
    so existing integrations port by swapping where ``cdp_url`` comes from.
    """

    handle: str
    session_id: str
    cdp_url: str
    expires_at: int
    policy_summary: dict[str, Any] | None = None


class CloseResult(_Model):
    closed: bool
    reason: str | None = None


class LoginSession(_Model):
    """A remote interactive login in progress.

    Hand ``live_view_url`` to the user; once they finish logging in,
    ``status`` becomes ``captured`` and ``session_id`` is populated.
    """

    login_id: str
    origin: str
    status: LoginStatus
    live_view_url: str
    session_id: str | None = None
    expires_at: int | None = None

    @property
    def is_terminal(self) -> bool:
        """True once the login can no longer change state."""
        return self.status in ("captured", "expired", "failed")


class Policy(_Model):
    origin: str
    yaml_body: str
    updated_at: int


class AuditEntry(_Model):
    timestamp: int
    event_type: str
    detail: str
    id: int | None = None
    session_id: str | None = None
    agent_id: str | None = None
    origin: str | None = None
