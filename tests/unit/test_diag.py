"""Tests for the structured diagnostic logger (``coral.diag``)."""

from __future__ import annotations

import io
import json

import pytest

from coral import diag


def test_log_event_emits_one_json_line() -> None:
    buf = io.StringIO()
    diag.log_event("info", "test.event", stream=buf, foo=1, bar="baz")
    line = buf.getvalue().strip()
    assert line.endswith("}")
    payload = json.loads(line)
    assert payload["level"] == "info"
    assert payload["event"] == "test.event"
    assert payload["foo"] == 1
    assert payload["bar"] == "baz"
    assert isinstance(payload["ts"], (int, float))


def test_log_event_filters_below_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORAL_DIAG_LEVEL", "warn")
    buf = io.StringIO()
    diag.log_event("info", "ignored", stream=buf)
    diag.log_event("warn", "kept", stream=buf)
    diag.log_event("error", "also.kept", stream=buf)
    lines = [line for line in buf.getvalue().splitlines() if line]
    assert len(lines) == 2
    assert {json.loads(line)["event"] for line in lines} == {"kept", "also.kept"}


def test_log_event_reserved_field_not_overwritten() -> None:
    buf = io.StringIO()
    # Caller can't directly pass level/event/ts (they're positional/reserved kwargs);
    # this test documents that user-supplied fields collide with reserved ones only
    # when bypassing the public API. Confirm we never silently shadow ``ts``.
    diag.log_event("info", "evt", stream=buf, ts=99)
    payload = json.loads(buf.getvalue().strip())
    assert payload["level"] == "info"
    assert payload["event"] == "evt"
    # ``ts`` reserved; user-supplied value ignored
    assert payload["ts"] != 99


def test_helpers_match_levels() -> None:
    buf = io.StringIO()
    diag.info("e1", stream=buf)
    diag.warn("e2", stream=buf)
    diag.error("e3", stream=buf)
    levels = [json.loads(line)["level"] for line in buf.getvalue().splitlines() if line]
    assert levels == ["info", "warn", "error"]
