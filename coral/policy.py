"""Per-site YAML policy engine (spec §4.3, §5.2).

Decisions exposed to the route handler and the MCP tools:

- ``allow``           — the navigation or action proceeds normally.
- ``deny``            — the route handler aborts; audit row written.
- ``review_required`` — a ``pending_reviews`` row is created; the agent gets
                        ``review_id`` and is expected to wait for an operator
                        to ``coral approve <review_id>``.

The engine is **pure**: it takes a parsed ``Policy`` and a URL or action and
returns a ``Decision``. No I/O. The route handler and MCP tools own the
side-effects (audit writes, pending-review inserts, rate-limit token state).

Spec §4.3 default is **allow** when no rule matches; ``default_action`` lets
power users flip this to ``deny`` for stricter policies (ADR-011).
"""

from __future__ import annotations

import fnmatch
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

Decision = Literal["allow", "deny", "review_required"]

DEFAULT_NAVIGATIONS_PER_MINUTE = 60
DEFAULT_ACTIONS_PER_MINUTE = 30
DEFAULT_ACTIONS_PER_HOUR = 500
DEFAULT_SESSION_MAX_DURATION_MINUTES = 60


class RateLimits(BaseModel):
    """Token-bucket replenish rates from the policy YAML (spec §4.3)."""

    actions_per_minute: int = DEFAULT_ACTIONS_PER_MINUTE
    actions_per_hour: int = DEFAULT_ACTIONS_PER_HOUR
    navigations_per_minute: int = DEFAULT_NAVIGATIONS_PER_MINUTE


class ReviewRule(BaseModel):
    """A rule that flags an action verb for human review (spec §4.3)."""

    action: str = Field(min_length=1)


class SessionRules(BaseModel):
    """Session-lifetime constraints from the policy YAML (spec §4.3)."""

    max_duration_minutes: int = DEFAULT_SESSION_MAX_DURATION_MINUTES
    # Track N (PR N3) redefined the semantic: when the daemon detects a
    # same-origin 401 mid-session, the session is flagged for user attention
    # (visible in the extension popup) instead of being torn down. The flag
    # name is preserved for backwards-compat with shipped behavior packs;
    # consider it "alert on staleness signal." See ADR-018.
    kill_on_redirect_to_login: bool = True


class Policy(BaseModel):
    """A validated per-origin policy document (spec §4.3)."""

    model_config = ConfigDict(extra="forbid")

    origin: str
    default_action: Literal["allow", "deny"] = "allow"
    allowed_paths: list[str] = Field(default_factory=lambda: [])
    denied_paths: list[str] = Field(default_factory=lambda: [])
    denied_actions: list[str] = Field(default_factory=lambda: [])
    review_required: list[ReviewRule] = Field(default_factory=lambda: [])
    rate_limits: RateLimits = Field(default_factory=RateLimits)
    session: SessionRules = Field(default_factory=SessionRules)

    @field_validator("origin")
    @classmethod
    def _validate_origin(cls, value: str) -> str:
        p = urlparse(value)
        if p.scheme not in {"http", "https"} or not p.netloc:
            raise ValueError("policy origin must be a full http(s) origin")
        return f"{p.scheme}://{p.netloc}"


def load_policy_yaml(*, origin: str, yaml_body: str) -> Policy:
    """Parse and validate a policy document. ``origin`` overrides any YAML value."""
    raw = yaml.safe_load(yaml_body)
    if not isinstance(raw, dict):
        raise ValueError("policy YAML must be a mapping at the top level")
    typed: dict[str, Any] = {**raw, "origin": origin}
    return Policy.model_validate(typed)


def _path_of(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pat) for pat in patterns)


@dataclass
class _Bucket:
    """Sliding-window counter for a single rate limit."""

    window_seconds: int
    limit: int
    hits: deque[float] = field(default_factory=lambda: deque(maxlen=4096))

    def take(self, now: float) -> bool:
        cutoff = now - self.window_seconds
        while self.hits and self.hits[0] < cutoff:
            self.hits.popleft()
        if len(self.hits) >= self.limit:
            return False
        self.hits.append(now)
        return True


@dataclass
class RateState:
    """Per-session rate-limit state (used by ``PolicyEngine``)."""

    nav_per_min: _Bucket
    act_per_min: _Bucket
    act_per_hour: _Bucket

    @classmethod
    def for_policy(cls, policy: Policy) -> RateState:
        rl = policy.rate_limits
        return cls(
            nav_per_min=_Bucket(60, rl.navigations_per_minute),
            act_per_min=_Bucket(60, rl.actions_per_minute),
            act_per_hour=_Bucket(3600, rl.actions_per_hour),
        )


class PolicyEngine:
    """Decision engine bound to a single (policy, rate-state) pair."""

    def __init__(self, policy: Policy, *, rate_state: RateState | None = None) -> None:
        self._policy = policy
        self._rate = rate_state or RateState.for_policy(policy)

    @property
    def policy(self) -> Policy:
        return self._policy

    @property
    def policy_summary(self) -> dict[str, Any]:
        """Compact summary returned to the agent in ``coral_open_session``."""
        return {
            "default_action": self._policy.default_action,
            "allowed_paths": list(self._policy.allowed_paths),
            "denied_paths": list(self._policy.denied_paths),
            "rate_limits": self._policy.rate_limits.model_dump(),
            "review_required_actions": [r.action for r in self._policy.review_required],
        }

    def evaluate_navigation(self, url: str, *, now: float | None = None) -> Decision:
        """Per-URL decision. Order: denied → allowed → default → rate limit."""
        now = time.time() if now is None else now
        path = _path_of(url)
        if _matches_any(path, self._policy.denied_paths):
            return "deny"
        if not self._rate.nav_per_min.take(now):
            return "deny"
        if _matches_any(path, self._policy.allowed_paths):
            return "allow"
        return self._policy.default_action

    def evaluate_action(
        self,
        action_type: str,
        *,
        now: float | None = None,
    ) -> Decision:
        """Per-verb decision. Order: denied → review_required → rate limit → default."""
        now = time.time() if now is None else now
        if action_type in self._policy.denied_actions:
            return "deny"
        if any(r.action == action_type for r in self._policy.review_required):
            return "review_required"
        if not self._rate.act_per_min.take(now):
            return "deny"
        if not self._rate.act_per_hour.take(now):
            return "deny"
        return self._policy.default_action


def default_policy_for_origin(origin: str) -> Policy:
    """Fallback when no row exists in ``policies`` for an origin."""
    return Policy(origin=origin)
