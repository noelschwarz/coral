"""Integration coverage for CLI bootstrap paths."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


def _pick_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_cli_start_healthz_subprocess(tmp_path) -> None:
    http_port = _pick_tcp_port()
    mcp_port = _pick_tcp_port()
    passphrase = "correct horse battery staple"

    env = os.environ.copy()
    env.update(
        {
            "CORAL_HOME": str(tmp_path),
            "CORAL_PASSPHRASE": passphrase,
            "CORAL_HTTP_PORT": str(http_port),
            "CORAL_MCP_HTTP_PORT": str(mcp_port),
        }
    )

    init = subprocess.run(
        [sys.executable, "-m", "coral", "init"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert init.returncode == 0, init.stderr

    proc = subprocess.Popen(
        [sys.executable, "-m", "coral", "start"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    url = f"http://127.0.0.1:{http_port}/healthz"
    deadline = time.monotonic() + 30.0
    status_payload = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as resp:
                status_payload = json.loads(resp.read().decode("utf-8"))
                break
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.05)
            if proc.poll() is not None:
                break

    assert status_payload is not None, "daemon failed to expose /healthz"
    assert status_payload.get("status") == "ok"
    assert "version" in status_payload

    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
