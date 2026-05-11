"""Append-only audit log (spec §3.1 Daemon + §4.1 ``audit_log`` table).

This module is the **single source of truth for audit writes.** Every audit
row in the codebase flows through ``write_audit_row`` (or its alias
``append_event``) so the row shape, serialization, and the no-secrets
discipline live in one place.

Callers that need the "fail-loudly on audit-write failure" HTTP-API semantics
wrap ``write_audit_row`` in a ``try`` block and raise an ``HTTPException`` from
the request handler; see ``coral.http_api._audit``.
"""

from __future__ import annotations

import json
import time
from typing import Any

from coral.models import AuditEntry
from coral.vault import Vault


def audit_detail(payload: dict[str, Any]) -> str:
    """Stable JSON serialization for the ``audit_log.detail`` column."""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


async def write_audit_row(
    vault: Vault,
    *,
    event_type: str,
    detail: dict[str, Any],
    session_id: str | None = None,
    agent_id: str | None = None,
    origin: str | None = None,
) -> None:
    """Insert one audit row. Caller owns the vault handle.

    Raises whatever the vault raises (typically ``VaultError``). HTTP handlers
    that need to turn this into a 500 wrap the call themselves.
    """
    entry = AuditEntry(
        timestamp=int(time.time()),
        session_id=session_id,
        agent_id=agent_id,
        event_type=event_type,
        origin=origin,
        detail=audit_detail(detail),
    )
    await vault.insert_audit(entry)


# Legacy name retained so existing call sites don't churn.
append_event = write_audit_row


async def fetch_since(vault: Vault, *, since_ts: int | None, limit: int) -> list[AuditEntry]:
    """Return audit rows with ``timestamp >= since_ts`` (HTTP ``GET /audit``)."""
    return await vault.query_audit(since=since_ts, limit=limit)
