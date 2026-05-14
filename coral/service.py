"""Install / uninstall Coral as an OS-managed user service.

macOS: ``launchd`` LaunchAgent at ``~/Library/LaunchAgents/dev.coralbridge.daemon.plist``.
Linux: ``systemd --user`` unit at ``~/.config/systemd/user/coralbridge.service``.

Windows: not supported in this track (Service Control Manager has different
ergonomics; see ADR-016).

Each install path writes a service file owned by the user (mode 0600 for the
plist on macOS since it can carry env vars, 0644 for the systemd unit), then
runs the OS-specific "load + enable + start" sequence.

We deliberately do **not** write the user's vault passphrase into the service
config. The service unit invokes ``coral start`` which prompts via TTY (or
reads ``CORAL_PASSPHRASE`` from the user's environment). Operationally this
means the user has two choices:

1. Run ``coral install-service`` and start the daemon manually after each
   reboot via ``coral up`` (no passphrase on disk).
2. Set ``CORAL_PASSPHRASE`` in their shell init / launchd plist
   ``EnvironmentVariables`` (passphrase on disk, mode 0600).

The CLI surfaces this choice as a confirm prompt at install time.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Platform(Enum):
    MACOS = "macos"
    LINUX = "linux"
    OTHER = "other"


def current_platform() -> Platform:
    if sys.platform == "darwin":
        return Platform.MACOS
    if sys.platform.startswith("linux"):
        return Platform.LINUX
    return Platform.OTHER


@dataclass(frozen=True)
class ServicePaths:
    """Filesystem locations the service installer touches."""

    label: str  # the launchd Label / systemd unit name (without extension)
    unit_path: Path


def _macos_paths() -> ServicePaths:
    home = Path.home()
    return ServicePaths(
        label="dev.coralbridge.daemon",
        unit_path=home / "Library" / "LaunchAgents" / "dev.coralbridge.daemon.plist",
    )


def _linux_paths() -> ServicePaths:
    home = Path.home()
    return ServicePaths(
        label="coralbridge",
        unit_path=home / ".config" / "systemd" / "user" / "coralbridge.service",
    )


def service_paths() -> ServicePaths:
    plat = current_platform()
    if plat is Platform.MACOS:
        return _macos_paths()
    if plat is Platform.LINUX:
        return _linux_paths()
    raise RuntimeError(
        "coral install-service supports macOS and Linux only. "
        "On Windows, run `coral up` manually or use Task Scheduler."
    )


def _macos_plist(
    *,
    label: str,
    coral_home: Path,
    coral_executable: list[str],
    passphrase_env: bool,
) -> str:
    """Build a launchd plist. ``coral_executable`` is the argv that runs the
    daemon, e.g. ``["/usr/local/bin/coral", "start"]``."""
    args_xml = "\n".join(f"        <string>{_xml_escape(a)}</string>" for a in coral_executable)
    log_path = coral_home / "coral.log"
    env_block = (
        "    <key>EnvironmentVariables</key>\n"
        "    <dict>\n"
        f"      <key>CORAL_HOME</key><string>{_xml_escape(str(coral_home))}</string>\n"
        + (
            "      <key>CORAL_PASSPHRASE</key>"
            "<string>__SET_THIS_IN_YOUR_PLIST_OR_USE_KEYCHAIN__</string>\n"
            if passphrase_env
            else ""
        )
        + "    </dict>"
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
{args_xml}
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
      <key>SuccessfulExit</key>
      <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>{_xml_escape(str(log_path))}</string>
    <key>StandardErrorPath</key>
    <string>{_xml_escape(str(log_path))}</string>
{env_block}
    <key>ProcessType</key>
    <string>Background</string>
  </dict>
</plist>
"""


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _linux_unit(
    *,
    coral_home: Path,
    coral_executable: list[str],
    passphrase_env: bool,
) -> str:
    """Build a systemd --user unit file."""
    exec_start = " ".join(_systemd_quote(a) for a in coral_executable)
    log_path = coral_home / "coral.log"
    env_lines = [f"Environment=CORAL_HOME={coral_home}"]
    if passphrase_env:
        env_lines.append("Environment=CORAL_PASSPHRASE=__SET_THIS_IN_YOUR_UNIT_OR_USE_KEYCHAIN__")
    env_block = "\n".join(env_lines)
    return f"""[Unit]
Description=Coral local-first session bridge daemon
After=default.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=5
StandardOutput=append:{log_path}
StandardError=append:{log_path}
{env_block}

[Install]
WantedBy=default.target
"""


def _systemd_quote(arg: str) -> str:
    """Escape an argument for a systemd ``ExecStart`` line."""
    if not arg or any(c in arg for c in " \t\"'\\$%"):
        return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return arg


def _coral_executable(coral_home: Path) -> list[str]:
    """Find the ``coral`` entry point to invoke from the service config.

    Prefers the installed console script (``coral`` on PATH). Falls back to
    ``<python> -m coral start`` if the script isn't found (typical for editable
    installs / ``uv run``).
    """
    coral = shutil.which("coral")
    if coral:
        return [coral, "start", "--home", str(coral_home)]
    return [sys.executable, "-m", "coral", "start", "--home", str(coral_home)]


@dataclass(frozen=True)
class InstallResult:
    label: str
    unit_path: Path
    needs_passphrase_edit: bool


def install_service(
    *,
    coral_home: Path,
    passphrase_env: bool,
) -> InstallResult:
    """Write the service file. Caller activates it via ``activate_service``.

    ``passphrase_env=True`` writes a ``CORAL_PASSPHRASE`` placeholder the user
    must edit. ``passphrase_env=False`` skips it — the daemon will fail to
    start until the user provides the passphrase another way.
    """
    paths = service_paths()
    plat = current_platform()
    executable = _coral_executable(coral_home)

    if plat is Platform.MACOS:
        body = _macos_plist(
            label=paths.label,
            coral_home=coral_home,
            coral_executable=executable,
            passphrase_env=passphrase_env,
        )
        mode = 0o600
    elif plat is Platform.LINUX:
        body = _linux_unit(
            coral_home=coral_home,
            coral_executable=executable,
            passphrase_env=passphrase_env,
        )
        mode = 0o600
    else:
        raise RuntimeError("unsupported platform")

    paths.unit_path.parent.mkdir(parents=True, exist_ok=True)
    paths.unit_path.write_text(body, encoding="utf-8")
    import contextlib as _contextlib

    with _contextlib.suppress(OSError):
        os.chmod(paths.unit_path, mode)
    return InstallResult(
        label=paths.label,
        unit_path=paths.unit_path,
        needs_passphrase_edit=passphrase_env,
    )


def activate_service() -> tuple[bool, str]:
    """Run the OS-specific 'load + enable + start' sequence. Returns
    ``(ok, message)``; ``message`` is captured stdout+stderr from the OS tool.
    """
    plat = current_platform()
    paths = service_paths()
    if plat is Platform.MACOS:
        # `launchctl bootstrap gui/<uid>` is the modern path; we fall back to
        # `load` on older macOS.
        uid = os.getuid()
        cmds = [
            ["launchctl", "bootstrap", f"gui/{uid}", str(paths.unit_path)],
        ]
    elif plat is Platform.LINUX:
        cmds = [
            ["systemctl", "--user", "daemon-reload"],
            ["systemctl", "--user", "enable", paths.label],
            ["systemctl", "--user", "start", paths.label],
        ]
    else:
        return False, "unsupported platform"
    out: list[str] = []
    for cmd in cmds:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"{cmd[0]} not available: {exc}"
        out.append(f"$ {' '.join(cmd)}\n{r.stdout}{r.stderr}")
        if r.returncode != 0:
            return False, "\n".join(out)
    return True, "\n".join(out)


def deactivate_service() -> tuple[bool, str]:
    """Stop + unload the service. Idempotent."""
    plat = current_platform()
    paths = service_paths()
    if plat is Platform.MACOS:
        uid = os.getuid()
        cmds = [
            ["launchctl", "bootout", f"gui/{uid}/{paths.label}"],
        ]
    elif plat is Platform.LINUX:
        cmds = [
            ["systemctl", "--user", "stop", paths.label],
            ["systemctl", "--user", "disable", paths.label],
        ]
    else:
        return False, "unsupported platform"
    out: list[str] = []
    for cmd in cmds:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            out.append(f"{cmd[0]}: {exc}")
            continue
        out.append(f"$ {' '.join(cmd)}\n{r.stdout}{r.stderr}")
    return True, "\n".join(out)


def uninstall_service() -> tuple[bool, str]:
    """Stop the service and remove the unit file."""
    ok, msg = deactivate_service()
    paths = service_paths()
    if paths.unit_path.is_file():
        paths.unit_path.unlink()
        msg = msg + f"\nremoved {paths.unit_path}"
    return ok, msg
