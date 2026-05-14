"""Unit tests for the cross-platform clipboard helper (Track K)."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from coral import clipboard


def test_copy_returns_false_when_no_tool_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no clipboard tool is on PATH, ``copy_to_clipboard`` returns False
    without raising."""
    monkeypatch.setattr(clipboard, "_available", lambda _cmd: False)
    assert clipboard.copy_to_clipboard("hello") is False


def test_helper_name_when_none_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clipboard, "_available", lambda _cmd: False)
    assert clipboard.clipboard_helper_name() == "none"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only path")
def test_macos_uses_pbcopy_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clipboard, "_available", lambda cmd: cmd == "pbcopy")
    called: dict[str, object] = {}

    def fake_copy(argv, text, **_kwargs):  # noqa: ANN001
        called["argv"] = list(argv)
        called["text"] = text
        return True

    monkeypatch.setattr(clipboard, "_try_copy", fake_copy)
    assert clipboard.copy_to_clipboard("hello") is True
    assert called["argv"] == ["pbcopy"]
    assert called["text"] == "hello"


def test_linux_prefers_wl_copy_then_xclip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    available: set[str] = {"xclip", "wl-copy"}  # wl-copy should win
    monkeypatch.setattr(clipboard, "_available", lambda cmd: cmd in available)
    captured: dict[str, object] = {}

    def fake_copy(argv, _text, **_kwargs):  # noqa: ANN001
        captured["argv"] = list(argv)
        return True

    monkeypatch.setattr(clipboard, "_try_copy", fake_copy)
    assert clipboard.copy_to_clipboard("x") is True
    assert captured["argv"][0] == "wl-copy"


def test_try_copy_handles_subprocess_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from subprocess import CompletedProcess

    def fake_run(*_args, **_kwargs):
        return CompletedProcess(args=[], returncode=1, stdout=b"", stderr=b"oops")

    with patch("subprocess.run", side_effect=fake_run):
        assert clipboard._try_copy(["bogus"], "x") is False
