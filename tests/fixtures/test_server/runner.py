"""Spawn the test server in a subprocess for integration/e2e tests.

The server is started in its own process so the test loop and the daemon's
event loop can both consume it without sharing port-bind state.
"""

from __future__ import annotations

import contextlib
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator


def _pick_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_ready(url: str, deadline_s: float) -> None:
    while time.monotonic() < deadline_s:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.05)
    raise TimeoutError(f"test server did not respond at {url}")


@contextlib.contextmanager
def run_test_server() -> Iterator[str]:
    """Yield the base URL of a running test server; tear it down on exit."""
    port = _pick_tcp_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.fixtures.test_server.server:build_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(f"{base}/", deadline_s=time.monotonic() + 15.0)
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
