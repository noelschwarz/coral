"""Per-site YAML policy engine (spec §4.3, §5.2).

Evaluates allowed/denied path patterns, action verbs, and rate limits. Default is
allow when no rule matches in v1 (spec §4.3).
"""

from __future__ import annotations

from typing import Any


def load_policy_yaml(*, origin: str, yaml_body: str) -> dict[str, Any]:
    """Parse and normalize a policy document stored for ``origin``."""
    raise NotImplementedError("Policy parsing ships in week 3 (spec §9).")


def evaluate_navigation(*, policy: dict[str, Any], path: str) -> str:
    """Return ``allow``, ``deny``, or ``review_required`` for a navigation target."""
    raise NotImplementedError("Path matching lands in week 3 (spec §9).")


def evaluate_action(*, policy: dict[str, Any], action: dict[str, Any]) -> str:
    """Return ``allow``, ``deny``, or ``review_required`` for a structured action."""
    raise NotImplementedError("Action verb evaluation lands in week 3 (spec §9).")
