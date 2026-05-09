"""End-to-end test for ``coral panic``.

Bootstraps a real daemon, captures a session via the handshake flow, runs
``coral panic --yes``, and verifies that:

  1. Every token is gone from the vault.
  2. Every session has status ``revoked``.
  3. The daemon is no longer running (PID file removed).
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

_GROUP = r"[A-HJ-NP-Z2-9]{4}"
CHALLENGE_RE = re.compile(rf"^\s+({_GROUP}-{_GROUP}-{_GROUP}-{_GROUP})")


def _pick_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _read_challenge(proc: subprocess.Popen, deadline_s: float) -> str:
    assert proc.stdout is not None
    while time.monotonic() < deadline_s:
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.05)
            continue
        m = CHALLENGE_RE.match(line)
        if m:
            return m.group(1)
    raise TimeoutError("did not see challenge on daemon stdout")


def _post_json(url: str, body: dict, headers: dict) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def _wait_for_health(url: str, deadline_s: float, proc: subprocess.Popen) -> None:
    while time.monotonic() < deadline_s:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        if proc.poll() is not None:
            raise RuntimeError("daemon exited before /healthz responded")
        time.sleep(0.1)
    raise TimeoutError(f"daemon did not expose {url} in time")


def test_coral_panic_revokes_everything(tmp_path) -> None:
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
            "PYTHONUNBUFFERED": "1",
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

    daemon = subprocess.Popen(
        [sys.executable, "-m", "coral", "start"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    try:
        deadline = time.monotonic() + 30.0
        challenge = _read_challenge(daemon, deadline)
        _wait_for_health(f"http://127.0.0.1:{http_port}/healthz", deadline, daemon)

        status, payload = _post_json(
            f"http://127.0.0.1:{http_port}/auth/handshake",
            {"challenge": challenge, "client_name": "extension"},
            {"Content-Type": "application/json"},
        )
        assert status == 200
        token = payload["token"]
        auth = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        # capture one session so panic has something to revoke
        status, _ = _post_json(
            f"http://127.0.0.1:{http_port}/sessions",
            {"origin": "https://example.com", "state": {"version": 1, "cookies": []}},
            auth,
        )
        assert status == 200

        # invoke `coral panic --yes`; expect zero exit
        result = subprocess.run(
            [sys.executable, "-m", "coral", "panic", "--yes"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr

        # daemon should exit shortly after panic SIGTERMs it
        try:
            daemon.wait(timeout=15)
        except subprocess.TimeoutExpired:
            daemon.kill()
            raise

    finally:
        if daemon.poll() is None:
            daemon.kill()
            daemon.wait(timeout=5)

    # Reopen the vault directly and verify state.
    import asyncio

    from coral.vault import unlock_vault

    async def _verify() -> None:
        vault = await unlock_vault(home=tmp_path, passphrase=passphrase)
        try:
            tokens = await vault.list_tokens()
            assert tokens == [], f"expected 0 tokens after panic, got {len(tokens)}"
            sessions = await vault.list_sessions()
            assert sessions, "session should still exist (revoked, not deleted)"
            assert all(s.status == "revoked" for s in sessions)
        finally:
            await vault.close()

    asyncio.run(_verify())
