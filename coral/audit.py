"""Append-only audit log helpers (spec §3.1, §4.1).

Most audit writes happen inline in the HTTP/MCP/sessions code paths so the
caller can correlate them with their own state. These module-level helpers
exist for code paths that don't already hold a vault reference.
"""

from __future__ import annotations

import json
import time
from typing import Any

from coral.models import AuditEntry
from coral.vault import Vault


async def append_event(
    vault: Vault,
    *,
    event_type: str,
    detail: dict[str, Any],
    session_id: str | None = None,
    agent_id: str | None = None,
    origin: str | None = None,
) -> None:
    """Insert a new audit row. Caller owns the vault handle."""
    entry = AuditEntry(
        timestamp=int(time.time()),
        session_id=session_id,
        agent_id=agent_id,
        event_type=event_type,
        origin=origin,
        detail=json.dumps(detail, separators=(",", ":"), sort_keys=True),
    )
    await vault.insert_audit(entry)


async def fetch_since(vault: Vault, *, since_ts: int | None, limit: int) -> list[AuditEntry]:
    """Return audit rows with ``timestamp >= since_ts`` (HTTP ``GET /audit``)."""
    return await vault.query_audit(since=since_ts, limit=limit)
