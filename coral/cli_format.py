"""Tiny formatting helpers for the CLI (Track M / PR M3).

Two utilities the CLI uses to make ``coral list`` and ``coral status``
readable instead of just functional:

- :func:`humanize_age` — turns a unix timestamp into "5m ago", "2h ago",
  "3d ago", etc. Returns ``"never"`` for ``None`` and ``"just now"`` for
  ages under one second.
- :func:`render_table` — fixed-width aligned table, no third-party deps,
  no ANSI escapes (callers add color separately if they want).

Kept deliberately small. If we ever want fancier output (truncation,
spinners, etc.) the right move is to pull in ``rich``, but for now this
is enough.
"""

from __future__ import annotations

import time


def humanize_age(ts: int | None, *, now: int | None = None) -> str:
    """Format a unix-second timestamp as a relative age string.

    ``None`` becomes ``"never"`` (Coral uses ``None`` for "this session has
    never been opened by an agent"). Future timestamps return ``"future"``
    rather than a negative duration.
    """
    if ts is None:
        return "never"
    current = now if now is not None else int(time.time())
    delta = current - ts
    if delta < 0:
        return "future"
    if delta < 1:
        return "just now"
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    if delta < 30 * 86400:
        return f"{delta // 86400}d ago"
    if delta < 365 * 86400:
        return f"{delta // (30 * 86400)}mo ago"
    return f"{delta // (365 * 86400)}y ago"


def render_table(
    headers: list[str],
    rows: list[list[str]],
    *,
    gap: int = 2,
) -> str:
    """Render a left-aligned table with one header row and an underline.

    Returns the empty string if ``rows`` is empty (caller handles the
    no-results message in a way that fits the surrounding command).
    """
    if not rows:
        return ""
    all_lines = [headers, *rows]
    widths = [max(len(line[i]) for line in all_lines) for i in range(len(headers))]
    sep = " " * gap

    def fmt_row(row: list[str]) -> str:
        return sep.join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip()

    out = [fmt_row(headers), sep.join("-" * w for w in widths)]
    out.extend(fmt_row(row) for row in rows)
    return "\n".join(out)


def short_id(value: str, *, length: int = 8) -> str:
    """Truncate a session/audit id for display. Keeps the leading bytes
    because Coral's UUIDs distribute entropy uniformly."""
    if len(value) <= length:
        return value
    return value[:length]
