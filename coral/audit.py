"""Append-only audit log (spec §3.1 Daemon + §4.1 ``audit_log`` table).

Records agent identity, Coral session identifiers, policy decisions, and lifecycle
events. Writes must be serialized with vault updates (spec §7.3).
"""

from __future__ import annotations

from typing import Any


async def append_event(
    *,
    event_type: str,
    detail: dict[str, Any],
    session_id: str | None,
    agent_id: str | None,
    origin: str | None,
) -> int:
    """Insert a new audit row and return its autoincrement id."""
    raise NotImplementedError("Audit persistence is wired in week 2 (spec §9).")


async def fetch_since(*, since_ts: int, limit: int) -> list[dict[str, Any]]:
    """Return audit rows with ``timestamp >= since_ts`` (HTTP ``GET /audit``)."""
    raise NotImplementedError("Audit querying lands with the HTTP API (spec §5.1).")
