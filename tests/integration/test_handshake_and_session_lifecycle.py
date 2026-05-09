"""End-to-end integration: full handshake + session lifecycle over HTTP.

Spawns ``coral start`` as a subprocess, parses the challenge from stdout, then drives
the daemon via real HTTP requests:

  handshake → POST /sessions → GET /sessions → DELETE /sessions/{id}
            → GET /audit (verify the event sequence) → SIGTERM
"""

from __future__ import annotations

import json
import os
import re
import signal
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


def _post(url: str, body: dict, headers: dict) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read().decode("utf-8")
            return resp.status, json.loads(payload) if payload else {}
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def _get(url: str, headers: dict) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def _delete(url: str, headers: dict) -> int:
    req = urllib.request.Request(url, headers=headers, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def test_full_handshake_capture_revoke_audit(tmp_path) -> None:
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

    proc = subprocess.Popen(
        [sys.executable, "-m", "coral", "start"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    try:
        deadline = time.monotonic() + 30.0
        challenge = _read_challenge(proc, deadline)
        _wait_for_health(f"http://127.0.0.1:{http_port}/healthz", deadline, proc)

        # 1. handshake
        status, payload = _post(
            f"http://127.0.0.1:{http_port}/auth/handshake",
            {"challenge": challenge, "client_name": "extension"},
            {"Content-Type": "application/json"},
        )
        assert status == 200, payload
        token = payload["token"]
        auth = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        # 2. capture a session
        status, payload = _post(
            f"http://127.0.0.1:{http_port}/sessions",
            {
                "origin": "https://example.com",
                "label": "demo",
                "state": {
                    "version": 1,
                    "cookies": [
                        {"name": "sid", "value": "abc", "expires": int(time.time()) + 3600}
                    ],
                    "local_storage": {},
                    "session_storage": {},
                },
            },
            auth,
        )
        assert status == 200, payload
        session_id = payload["session_id"]

        # 3. list returns it without state_blob
        status, payload = _get(f"http://127.0.0.1:{http_port}/sessions", auth)
        assert status == 200
        assert any(s["id"] == session_id for s in payload["sessions"])
        for s in payload["sessions"]:
            assert "state_blob" not in s

        # 4. revoke it
        status_code = _delete(f"http://127.0.0.1:{http_port}/sessions/{session_id}", auth)
        assert status_code == 204

        # 5. audit log records the sequence
        status, payload = _get(f"http://127.0.0.1:{http_port}/audit?limit=100", auth)
        assert status == 200
        types = [e["event_type"] for e in payload["entries"]]
        for expected in (
            "auth.handshake.success",
            "session.captured",
            "session.list",
            "session.revoked",
        ):
            assert expected in types, f"missing {expected} in {types}"

    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
