"""Playwright-backed session server (spec §3.1 Daemon).

Owns the singleton Chromium instance, isolated browser contexts, session state
restoration, CDP endpoint plumbing, and route-handler hooks for policy enforcement.
"""

from __future__ import annotations

from typing import Any


async def open_isolated_context(*, session_id: str, purpose: str) -> dict[str, Any]:
    """Create an isolated Playwright context and restore a vaulted session into it.

    Planned implementation (spec §7.2):
    validate session → evaluate policy → create context → install route handlers →
    restore storage → expose CDP URL → write audit row.
    """
    raise NotImplementedError("Session contexts are implemented in week 2 (spec §9).")


async def close_context(*, session_handle: str) -> None:
    """Tear down an open Playwright session context and persist audit entries."""
    raise NotImplementedError("Session teardown is implemented in week 2 (spec §9).")


def recovery_kill_orphan_browsers() -> int:
    """Best-effort cleanup for Chromium processes tagged by Coral (spec §7.4)."""
    raise NotImplementedError("Orphan recovery lands with Playwright integration (spec §7.4).")
