"""Cross-platform clipboard copy for the CLI.

Tries (in order):
- macOS: ``pbcopy``
- Linux: ``xclip`` → ``xsel`` → ``wl-copy``
- Windows: ``clip``

Returns ``True`` on success. Never raises — clipboard copy is a nice-to-have,
not a critical path. Caller decides what to do when it fails (typically: print
the text plainly and tell the user to copy it by hand).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Sequence


def _available(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _try_copy(argv: Sequence[str], text: str, *, timeout: float = 2.0) -> bool:
    try:
        proc = subprocess.run(
            list(argv),
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def copy_to_clipboard(text: str) -> bool:
    """Best-effort clipboard copy. ``True`` on success."""
    if sys.platform == "darwin" and _available("pbcopy"):
        return _try_copy(["pbcopy"], text)
    if sys.platform == "win32" and _available("clip"):
        return _try_copy(["clip"], text)
    # Linux: try the popular X11 / Wayland tools in order.
    if _available("wl-copy"):
        return _try_copy(["wl-copy"], text)
    if _available("xclip"):
        return _try_copy(["xclip", "-selection", "clipboard"], text)
    if _available("xsel"):
        return _try_copy(["xsel", "--clipboard", "--input"], text)
    return False


def clipboard_helper_name() -> str:
    """Human-readable description of which clipboard tool is in use, or
    ``"none"`` if no tool is available. Used by ``coral diagnose``."""
    if sys.platform == "darwin":
        return "pbcopy" if _available("pbcopy") else "none"
    if sys.platform == "win32":
        return "clip" if _available("clip") else "none"
    for name in ("wl-copy", "xclip", "xsel"):
        if _available(name):
            return name
    return "none"
