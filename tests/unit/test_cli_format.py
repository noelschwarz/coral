"""Tests for the small CLI formatting helpers (Track M / PR M3)."""

from __future__ import annotations

from coral.cli_format import humanize_age, render_table, short_id

# ---- humanize_age ----------------------------------------------------------


def test_humanize_age_none_is_never() -> None:
    assert humanize_age(None) == "never"


def test_humanize_age_just_now() -> None:
    assert humanize_age(1_000_000, now=1_000_000) == "just now"


def test_humanize_age_seconds() -> None:
    assert humanize_age(1_000_000, now=1_000_030) == "30s ago"


def test_humanize_age_minutes() -> None:
    assert humanize_age(1_000_000, now=1_000_000 + 5 * 60) == "5m ago"


def test_humanize_age_hours() -> None:
    assert humanize_age(1_000_000, now=1_000_000 + 3 * 3600) == "3h ago"


def test_humanize_age_days() -> None:
    assert humanize_age(1_000_000, now=1_000_000 + 4 * 86400) == "4d ago"


def test_humanize_age_months() -> None:
    assert humanize_age(1_000_000, now=1_000_000 + 90 * 86400) == "3mo ago"


def test_humanize_age_years() -> None:
    assert humanize_age(1_000_000, now=1_000_000 + 400 * 86400) == "1y ago"


def test_humanize_age_future_is_future() -> None:
    assert humanize_age(2_000_000, now=1_000_000) == "future"


# ---- render_table ----------------------------------------------------------


def test_render_table_empty_returns_empty() -> None:
    assert render_table(["A", "B"], []) == ""


def test_render_table_aligns_columns() -> None:
    out = render_table(
        ["ID", "ORIGIN"],
        [["abc", "https://github.com"], ["x", "https://gmail.com"]],
    )
    lines = out.split("\n")
    # Header row, separator row, two data rows.
    assert len(lines) == 4
    assert lines[0].startswith("ID")
    assert "ORIGIN" in lines[0]
    assert set(lines[1]) <= {"-", " "}
    # Both data rows must have the origin column starting at the same offset.
    offset_a = lines[2].index("https://github.com")
    offset_b = lines[3].index("https://gmail.com")
    assert offset_a == offset_b


def test_render_table_widens_for_longest_cell() -> None:
    """The separator row's length reflects the widest column's width."""
    out = render_table(
        ["X", "Y"],
        [["short", "a"], ["b", "a much longer cell"]],
    )
    lines = out.split("\n")
    # Separator row: dashes for col 1 width (5 = "short") + gap + dashes for
    # col 2 width (18 = "a much longer cell").
    assert lines[1] == "-----  ------------------"


# ---- short_id --------------------------------------------------------------


def test_short_id_truncates_long_values() -> None:
    assert short_id("abcdef1234567890") == "abcdef12"


def test_short_id_passes_through_short_values() -> None:
    assert short_id("abc") == "abc"


def test_short_id_custom_length() -> None:
    assert short_id("abcdef1234", length=4) == "abcd"
