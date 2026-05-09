"""Translate captured ``state_blob`` payloads back into a Playwright context.

The conversion is deliberately small and total:

- **Cookies** are reshaped from the §4.2 schema into Playwright's ``add_cookies``
  shape (camelCase fields, normalized ``sameSite`` values, ``expires`` clamped
  to a numeric float).
- **localStorage / sessionStorage** are seeded by injecting an init script that
  calls ``Storage.setItem`` *before* the first navigation runs. ``add_init_script``
  on the context (not the page) ensures every page in the context picks it up.

IndexedDB and service workers are intentionally not handled: the engineering
spec §6.4 marks them best-effort-deferred for v1.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext


_VALID_SAME_SITE = {"Strict", "Lax", "None"}


def _normalize_same_site(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().capitalize()
    return s if s in _VALID_SAME_SITE else None


def _coerce_expires(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _convert_cookie(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    typed: dict[str, Any] = cast(dict[str, Any], raw)
    name = typed.get("name")
    value = typed.get("value")
    domain = typed.get("domain")
    if not (isinstance(name, str) and isinstance(value, str) and isinstance(domain, str)):
        return None
    path_raw = typed.get("path")
    cookie: dict[str, Any] = {
        "name": name,
        "value": value,
        "domain": domain,
        "path": path_raw if isinstance(path_raw, str) else "/",
    }
    expires = _coerce_expires(typed.get("expires"))
    if expires is not None:
        cookie["expires"] = expires
    http_only_raw = typed.get("httpOnly")
    if http_only_raw is None:
        http_only_raw = typed.get("http_only")
    if http_only_raw is not None:
        cookie["httpOnly"] = bool(http_only_raw)
    secure_raw = typed.get("secure")
    if secure_raw is not None:
        cookie["secure"] = bool(secure_raw)
    same_site = _normalize_same_site(typed.get("sameSite") or typed.get("same_site"))
    if same_site is not None:
        cookie["sameSite"] = same_site
    return cookie


def cookies_to_playwright(state_blob: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert §4.2 cookie shape into Playwright's ``add_cookies`` argument."""
    raw_cookies_obj: Any = state_blob.get("cookies") or []
    if not isinstance(raw_cookies_obj, list):
        return []
    raw_cookies: list[Any] = cast(list[Any], raw_cookies_obj)
    out: list[dict[str, Any]] = []
    for raw in raw_cookies:
        converted = _convert_cookie(raw)
        if converted is not None:
            out.append(converted)
    return out


def _str_dict(value: Any) -> dict[str, str]:
    """Coerce a parsed-JSON value to a flat ``{str: str}`` dict; drop the rest."""
    if not isinstance(value, dict):
        return {}
    typed: dict[str, Any] = cast(dict[str, Any], value)
    out: dict[str, str] = {}
    for k, v in typed.items():
        if v is not None:
            out[k] = str(v)
    return out


def storage_init_script(state_blob: dict[str, Any]) -> str | None:
    """Build the JS init script that seeds local/session storage.

    Returns ``None`` if neither storage bucket has any keys — saves an unnecessary
    init-script registration.
    """
    local = _str_dict(state_blob.get("local_storage"))
    session = _str_dict(state_blob.get("session_storage"))
    if not local and not session:
        return None
    payload = {"local": local, "session": session}
    encoded = json.dumps(payload, separators=(",", ":"))
    return (
        "(() => {\n"
        f"  const __coral_blob = {encoded};\n"
        "  try {\n"
        "    for (const [k, v] of Object.entries(__coral_blob.local)) {\n"
        "      localStorage.setItem(k, v);\n"
        "    }\n"
        "  } catch (_) {}\n"
        "  try {\n"
        "    for (const [k, v] of Object.entries(__coral_blob.session)) {\n"
        "      sessionStorage.setItem(k, v);\n"
        "    }\n"
        "  } catch (_) {}\n"
        "})();"
    )


async def apply_state_blob(context: BrowserContext, state_blob: dict[str, Any]) -> None:
    """Restore cookies and storage onto a fresh Playwright context.

    Order matters: cookies before init scripts, init scripts before any
    ``page.goto``. The caller is responsible for not navigating before this
    coroutine returns.
    """
    cookies = cookies_to_playwright(state_blob)
    if cookies:
        await context.add_cookies(cookies)  # type: ignore[arg-type]
    init = storage_init_script(state_blob)
    if init is not None:
        await context.add_init_script(init)
