"""Structured operational logging (separate from the user-visible audit log).

The audit log records *what users care about*: every authenticated request, every
session lifecycle event, every policy decision. It lives in the encrypted vault
and is queryable via ``GET /audit`` or the ``coral audit`` CLI.

Operational logging records *what operators care about*: daemon startup/shutdown,
transport-level decisions (handshake rate limit, auth-middleware rejections),
vault-open timing, MCP transport state. It goes to stderr as one JSON line per
event so it can be redirected to a file, piped through ``jq``, or shipped to a log
aggregator without touching the audit log.

Discipline (mirrors audit):
- Never log tokens, challenges, passphrases, state_blob contents, or anything
  reversible to a credential.
- Lower-cardinality is better: counts, reasons, and IDs (token name, agent name)
  rather than full payloads.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Literal, TextIO

Level = Literal["debug", "info", "warn", "error"]

_LEVEL_RANK: dict[Level, int] = {"debug": 10, "info": 20, "warn": 30, "error": 40}

_DEFAULT_STREAM: TextIO = sys.stderr


def _min_level() -> int:
    raw = os.environ.get("CORAL_DIAG_LEVEL", "info").lower().strip()
    return _LEVEL_RANK.get(raw, 20)  # type: ignore[arg-type]


def log_event(
    level: Level,
    event: str,
    *,
    stream: TextIO | None = None,
    **fields: Any,
) -> None:
    """Emit a structured diagnostic event to stderr.

    ``event`` is a dotted name (``daemon.start``, ``mcp.auth.rejected``) parallel
    to the audit-log taxonomy but disjoint — diagnostics never duplicate audit.
    Filter via ``CORAL_DIAG_LEVEL=debug|info|warn|error`` (default ``info``).
    """
    if _LEVEL_RANK[level] < _min_level():
        return
    payload: dict[str, Any] = {
        "ts": time.time(),
        "level": level,
        "event": event,
    }
    for key, value in fields.items():
        if key in payload:
            continue
        payload[key] = value
    line = json.dumps(payload, separators=(",", ":"), default=str)
    out = stream if stream is not None else _DEFAULT_STREAM
    print(line, file=out, flush=True)


def info(event: str, **fields: Any) -> None:
    log_event("info", event, **fields)


def warn(event: str, **fields: Any) -> None:
    log_event("warn", event, **fields)


def error(event: str, **fields: Any) -> None:
    log_event("error", event, **fields)


def debug(event: str, **fields: Any) -> None:
    log_event("debug", event, **fields)
