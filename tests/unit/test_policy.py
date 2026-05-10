"""Unit + property-based tests for the policy engine (Track E)."""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from coral.policy import (
    Policy,
    PolicyEngine,
    RateLimits,
    ReviewRule,
    SessionRules,
    default_policy_for_origin,
    load_policy_yaml,
)


def _engine(
    *,
    default_action: str = "deny",
    allowed: list[str] | None = None,
    denied: list[str] | None = None,
    denied_actions: list[str] | None = None,
    review_required: list[str] | None = None,
    rate_limits: RateLimits | None = None,
) -> PolicyEngine:
    policy = Policy(
        origin="https://example.com",
        default_action=default_action,  # type: ignore[arg-type]
        allowed_paths=allowed or [],
        denied_paths=denied or [],
        denied_actions=denied_actions or [],
        review_required=[ReviewRule(action=a) for a in (review_required or [])],
        rate_limits=rate_limits or RateLimits(),
        session=SessionRules(),
    )
    return PolicyEngine(policy)


# YAML parsing --------------------------------------------------------------


def test_load_policy_yaml_minimal() -> None:
    yaml_body = "default_action: allow\n"
    policy = load_policy_yaml(origin="https://example.com", yaml_body=yaml_body)
    assert policy.origin == "https://example.com"
    assert policy.default_action == "allow"
    assert policy.allowed_paths == []


def test_load_policy_yaml_overrides_origin_field() -> None:
    yaml_body = "origin: https://hacked.example\n"
    policy = load_policy_yaml(origin="https://example.com", yaml_body=yaml_body)
    assert policy.origin == "https://example.com"


def test_load_policy_yaml_rejects_garbage_top_level() -> None:
    with pytest.raises(ValueError, match="mapping"):
        load_policy_yaml(origin="https://example.com", yaml_body="- not a mapping")


def test_policy_rejects_unknown_origin_scheme() -> None:
    with pytest.raises(ValueError):
        Policy(origin="ftp://example.com")


def test_load_policy_yaml_rejects_unknown_fields() -> None:
    from pydantic import ValidationError

    yaml_body = "wat_is_dis: true\n"
    with pytest.raises(ValidationError):
        load_policy_yaml(origin="https://example.com", yaml_body=yaml_body)


# Navigation evaluation -----------------------------------------------------


def test_navigation_denied_before_allowed() -> None:
    e = _engine(
        default_action="allow",
        allowed=["/feed/*"],
        denied=["/feed/private/*"],
    )
    assert e.evaluate_navigation("https://example.com/feed/public") == "allow"
    assert e.evaluate_navigation("https://example.com/feed/private/x") == "deny"


def test_navigation_default_allow_when_no_rules() -> None:
    e = _engine(default_action="allow")
    assert e.evaluate_navigation("https://example.com/anywhere") == "allow"


def test_navigation_default_deny_when_no_rules() -> None:
    e = _engine(default_action="deny")
    assert e.evaluate_navigation("https://example.com/anywhere") == "deny"


def test_navigation_default_deny_with_allowlist() -> None:
    e = _engine(default_action="deny", allowed=["/feed/*"])
    assert e.evaluate_navigation("https://example.com/feed/x") == "allow"
    assert e.evaluate_navigation("https://example.com/jobs") == "deny"


def test_navigation_rate_limit_kicks_in() -> None:
    e = _engine(
        default_action="allow",
        rate_limits=RateLimits(navigations_per_minute=2),
    )
    base = "https://example.com/feed"
    assert e.evaluate_navigation(base, now=1000.0) == "allow"
    assert e.evaluate_navigation(base, now=1001.0) == "allow"
    assert e.evaluate_navigation(base, now=1002.0) == "deny"
    # outside the 60s window, the budget replenishes
    assert e.evaluate_navigation(base, now=1065.0) == "allow"


# Action evaluation ---------------------------------------------------------


def test_action_denied_action() -> None:
    e = _engine(denied_actions=["delete_account"])
    assert e.evaluate_action("delete_account") == "deny"


def test_action_review_required() -> None:
    e = _engine(review_required=["post_content"])
    assert e.evaluate_action("post_content") == "review_required"


def test_action_default_when_unconfigured() -> None:
    e = _engine(default_action="allow")
    assert e.evaluate_action("read_feed") == "allow"
    e2 = _engine(default_action="deny")
    assert e2.evaluate_action("read_feed") == "deny"


def test_action_rate_limit_minute() -> None:
    e = _engine(
        default_action="allow",
        rate_limits=RateLimits(actions_per_minute=2, actions_per_hour=10),
    )
    assert e.evaluate_action("do", now=1000.0) == "allow"
    assert e.evaluate_action("do", now=1001.0) == "allow"
    assert e.evaluate_action("do", now=1002.0) == "deny"


# Property-based: spec §8.2 ------------------------------------------------


_PATH_PART = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Nd"), whitelist_characters="-_"),
    min_size=1,
    max_size=8,
).map(lambda s: "/" + s)

_PATH = st.lists(_PATH_PART, min_size=1, max_size=4).map(lambda parts: "".join(parts))


@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None, max_examples=200)
@given(deny=st.lists(_PATH, max_size=4), allow=st.lists(_PATH, max_size=4), probe=_PATH)
def test_property_explicit_deny_always_wins(deny: list[str], allow: list[str], probe: str) -> None:
    """If the probe path matches any denied glob, the decision is always 'deny'."""
    e = _engine(default_action="allow", denied=deny, allowed=allow + [probe])
    if any(probe == d for d in deny):  # exact match guaranteed-deny
        assert e.evaluate_navigation("https://example.com" + probe) == "deny"


@settings(suppress_health_check=[HealthCheck.too_slow], deadline=None, max_examples=200)
@given(
    default=st.sampled_from(["allow", "deny"]),
    probe=_PATH,
)
def test_property_default_action_holds_when_no_rules_match(default: str, probe: str) -> None:
    """No rules ⇒ decision == default_action (modulo rate limit, which is huge)."""
    e = _engine(default_action=default)
    assert e.evaluate_navigation("https://example.com" + probe) == default


# Default policy ------------------------------------------------------------


def test_default_policy_for_origin() -> None:
    p = default_policy_for_origin("https://example.com")
    assert p.origin == "https://example.com"
    assert p.default_action == "allow"  # spec §4.3
    assert p.allowed_paths == []
    assert p.denied_paths == []


# Policy summary surface ----------------------------------------------------


def test_policy_summary_is_compact() -> None:
    e = _engine(
        default_action="deny",
        allowed=["/feed/*"],
        denied=["/settings/*"],
        review_required=["post"],
    )
    summary = e.policy_summary
    assert summary["default_action"] == "deny"
    assert summary["allowed_paths"] == ["/feed/*"]
    assert summary["denied_paths"] == ["/settings/*"]
    assert summary["review_required_actions"] == ["post"]
    assert "rate_limits" in summary


# Bundled packs round-trip --------------------------------------------------


@pytest.mark.parametrize(
    "name,origin",
    [
        ("github", "https://github.com"),
        ("gmail", "https://mail.google.com"),
        ("linear", "https://linear.app"),
        ("linkedin", "https://www.linkedin.com"),
        ("notion", "https://www.notion.so"),
        ("slack", "https://app.slack.com"),
    ],
)
def test_bundled_packs_parse(name: str, origin: str) -> None:
    from pathlib import Path

    body = (Path("coral/behavior_packs") / f"{name}.yaml").read_text()
    policy = load_policy_yaml(origin=origin, yaml_body=body)
    assert policy.origin == origin
    assert policy.default_action == "deny"
    assert policy.allowed_paths
    assert policy.review_required
