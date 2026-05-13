"""Tests for orphan-Chromium recovery (spec §7.4, Track G)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psutil
import pytest

from coral.sessions import CORAL_DAEMON_HOME_ENV, recovery_kill_orphan_browsers


class _FakeProc:
    """Stands in for a psutil.Process with controllable env / name."""

    def __init__(self, *, pid: int, name: str, environ: dict[str, str] | None = None) -> None:
        self.info = {"pid": pid, "name": name}
        self._env = environ or {}
        self.killed = False
        self.raise_on_environ: type[Exception] | None = None

    def environ(self) -> dict[str, str]:
        if self.raise_on_environ is not None:
            raise self.raise_on_environ()
        return self._env

    def kill(self) -> None:
        self.killed = True


def _patch_process_iter(monkeypatch: pytest.MonkeyPatch, procs: list[_FakeProc]) -> None:
    def fake_iter(attrs: Any = None) -> Any:  # noqa: ARG001
        return iter(procs)

    monkeypatch.setattr(psutil, "process_iter", fake_iter)


def test_no_processes_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_process_iter(monkeypatch, [])
    assert recovery_kill_orphan_browsers(Path("/coral/home")) == 0


def test_matching_chromium_is_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    target = Path("/coral/home")
    procs = [
        _FakeProc(pid=1, name="chromium", environ={CORAL_DAEMON_HOME_ENV: str(target)}),
        _FakeProc(pid=2, name="chrome", environ={CORAL_DAEMON_HOME_ENV: str(target)}),
    ]
    _patch_process_iter(monkeypatch, procs)
    killed = recovery_kill_orphan_browsers(target)
    assert killed == 2
    assert all(p.killed for p in procs)


def test_other_coral_home_is_not_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    target = Path("/coral/home")
    other = _FakeProc(pid=10, name="chromium", environ={CORAL_DAEMON_HOME_ENV: "/coral/other"})
    _patch_process_iter(monkeypatch, [other])
    assert recovery_kill_orphan_browsers(target) == 0
    assert other.killed is False


def test_untagged_chromium_is_not_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    """User's regular Chrome with no CORAL_DAEMON_HOME env var is left alone."""
    target = Path("/coral/home")
    regular_chrome = _FakeProc(pid=20, name="chrome", environ={})
    _patch_process_iter(monkeypatch, [regular_chrome])
    assert recovery_kill_orphan_browsers(target) == 0
    assert regular_chrome.killed is False


def test_non_chromium_processes_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    target = Path("/coral/home")
    procs = [
        _FakeProc(pid=30, name="firefox", environ={CORAL_DAEMON_HOME_ENV: str(target)}),
        _FakeProc(pid=31, name="bash", environ={CORAL_DAEMON_HOME_ENV: str(target)}),
    ]
    _patch_process_iter(monkeypatch, procs)
    assert recovery_kill_orphan_browsers(target) == 0
    assert not any(p.killed for p in procs)


def test_access_denied_on_environ_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    target = Path("/coral/home")
    p = _FakeProc(pid=40, name="chromium")
    p.raise_on_environ = psutil.AccessDenied
    _patch_process_iter(monkeypatch, [p])
    # Must not raise; just skip.
    assert recovery_kill_orphan_browsers(target) == 0
    assert p.killed is False


def test_macos_process_name_matched(monkeypatch: pytest.MonkeyPatch) -> None:
    target = Path("/coral/home")
    mac_proc = _FakeProc(pid=50, name="Google Chrome", environ={CORAL_DAEMON_HOME_ENV: str(target)})
    _patch_process_iter(monkeypatch, [mac_proc])
    assert recovery_kill_orphan_browsers(target) == 1
    assert mac_proc.killed
