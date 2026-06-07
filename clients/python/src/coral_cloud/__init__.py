"""Coral Cloud — Python SDK.

Give AI agents authenticated browser sessions with an API key. See
``protocol/openapi.yaml`` for the wire contract and
``docs/ADR-019-cloud-architecture.md`` for the design.
"""

from __future__ import annotations

from ._transport import Response, Transport
from ._version import __version__
from .client import DEFAULT_BASE_URL, CoralCloud
from .exceptions import (
    CoralAPIError,
    CoralAuthError,
    CoralConfigError,
    CoralConflictError,
    CoralError,
    CoralNotFoundError,
    CoralRateLimitError,
)
from .models import (
    AuditEntry,
    CloseResult,
    LoginSession,
    OpenSession,
    Policy,
    Session,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "AuditEntry",
    "CloseResult",
    "CoralAPIError",
    "CoralAuthError",
    "CoralCloud",
    "CoralConfigError",
    "CoralConflictError",
    "CoralError",
    "CoralNotFoundError",
    "CoralRateLimitError",
    "LoginSession",
    "OpenSession",
    "Policy",
    "Response",
    "Session",
    "Transport",
    "__version__",
]
