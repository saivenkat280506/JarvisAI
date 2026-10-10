"""
sys_platform.py — macOS primitive facade (MCP-routed).
=====================================================
The canonical brief forbids bare subprocess / osascript / pyautogui calls
in backend code. This module keeps the historical `executor.sys_platform`
import surface for every consumer (volume_control, automation, task_runner,
open_app, puppeteer_client, search_briefing, process_monitor, routes) while
routing all OS *actions* through the `macos` MCP server:

  - Pure helpers / constants (desktop_dir, chrome paths, aliases…) are
    imported directly from mcp_servers.macos_native — no execution involved.
  - Action functions are thin proxies: mcp_clients.call("macos", name, args).
    Results are revived to their old shapes (CompletedProcess-like, tuples,
    sets) so call sites needed no type changes.

The only process-spawning code in the repo now lives in backend/mcp_servers/.
"""

from __future__ import annotations

from typing import Any, Iterable

from mcp_clients import registry
from mcp_servers.macos_native import (
    IS_MAC,
    IS_WIN,
    MOD_KEY,
    MAC_APP_ALIASES,
    chrome_executable,
    chrome_profile_directory,
    chrome_profile_directory_for_email,
    chrome_user_data,
    current_user,
    desktop_dir,
    python_venv,
    resolve_mac_app,
    which_or,
)

__all__ = [
    "IS_MAC", "IS_WIN", "MOD_KEY", "MAC_APP_ALIASES",
    "chrome_executable", "chrome_profile_directory", "chrome_profile_directory_for_email", "chrome_user_data",
    "current_user", "desktop_dir", "python_venv", "resolve_mac_app", "which_or",
    "osascript", "run_open", "open_uri", "open_url", "open_application", "close_application",
    "focus_application", "list_visible_windows", "get_work_area",
    "set_app_window_bounds", "list_process_names", "pids_matching",
    "is_app_running", "accessibility_available", "window_bounds",
    "set_clipboard", "get_clipboard", "keystroke_in_app", "kill_named",
    "kill_matching_command", "get_output_volume", "is_output_muted",
    "set_output_volume", "set_output_muted", "system_power", "notify",
    "quit_google_chrome",
]


class CompletedProcess:
    """Attribute-compatible revival of subprocess.CompletedProcess results
    (built by the MCP server's JSON encoder, rebuilt here — the executor
    itself never touches subprocess)."""

    def __init__(self, args: Any, returncode: int, stdout: str | None,
                 stderr: str | None):
        self.args = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _call(name: str, *, _timeout: float = 30.0, **args: Any) -> Any:
    return registry.call("macos", name, args, timeout=_timeout)


def _cp(raw: Any) -> CompletedProcess:
    if isinstance(raw, dict):
        return CompletedProcess(raw.get("args"), int(raw.get("returncode") or 0),
                                raw.get("stdout"), raw.get("stderr"))
    return CompletedProcess(None, 0, str(raw) if raw is not None else None, None)


def _pair(raw: Any) -> tuple[bool, str]:
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        return bool(raw[0]), str(raw[1])
    return bool(raw), ""


# ── AppleScript / shell-open actions ─────────────────────────────────────

def osascript(source: str, timeout: float = 8.0) -> CompletedProcess:
    return _cp(_call("osascript", _timeout=timeout + 15.0,
                     source=source, timeout=timeout))


def run_open(*args: str) -> CompletedProcess:
    return _cp(_call("run_open", _timeout=25.0, args=list(args)))


def open_uri(uri: str) -> bool:
    return bool(_call("open_uri", uri=uri))


def open_url(url: str) -> bool:
    """Open a URL in the default browser (MCP-routed — replaces the old
    `webbrowser.open` calls so every launch lands in the audit log).

    Tries the shell MCP `open` first (covers http/https and files); falls
    back to the macos MCP AppleScript `open location` for bare schemes such
    as about:blank that `open` treats as file paths."""
    from mcp_clients import shell as mcp_shell
    try:
        res = mcp_shell.open_path(url)
        if isinstance(res, dict) and res.get("ok"):
            return True
    except Exception:
        pass
    try:
        escaped = str(url).replace("\\", "\\\\").replace('"', '\\"')
        return osascript(f'open location "{escaped}"').ok
    except Exception:
        return False


def open_application(app_name: str) -> tuple[bool, str]:
    return _pair(_call("open_application", app_name=app_name, _timeout=35.0))


def close_application(app_name: str) -> tuple[bool, str]:
    return _pair(_call("close_application", app_name=app_name, _timeout=35.0))


def focus_application(app_name: str) -> tuple[bool, str]:
    return _pair(_call("focus_application", app_name=app_name, _timeout=35.0))


def quit_google_chrome(timeout: float = 8.0) -> None:
    _call("quit_google_chrome", _timeout=timeout + 15.0, timeout=timeout)


# ── Window / process inspection ──────────────────────────────────────────

def list_visible_windows() -> str:
    return str(_call("list_visible_windows", _timeout=25.0))


def get_work_area() -> dict[str, int]:
    return dict(_call("get_work_area", _timeout=15.0))


def set_app_window_bounds(app_hint: str, x: int, y: int, width: int,
                          height: int) -> bool:
    return bool(_call("set_app_window_bounds", app_hint=app_hint, x=x, y=y,
                      width=width, height=height, _timeout=25.0))


def window_bounds(app_name: str) -> dict | None:
    raw = _call("window_bounds", app_name=app_name, _timeout=25.0)
    return dict(raw) if isinstance(raw, dict) else None


def list_process_names() -> set[str]:
    return set(_call("list_process_names", _timeout=25.0) or [])


def pids_matching(pattern: str) -> set[int]:
    return {int(p) for p in (_call("pids_matching", _timeout=15.0,
                                   pattern=pattern) or [])}


def is_app_running(app_name: str) -> bool:
    return bool(_call("is_app_running", app_name=app_name))


def accessibility_available() -> bool:
    return bool(_call("accessibility_available"))


# ── Clipboard / keyboard ─────────────────────────────────────────────────

def set_clipboard(text: str) -> bool:
    return bool(_call("set_clipboard", text=text))


def get_clipboard() -> str:
    return str(_call("get_clipboard") or "")


def keystroke_in_app(app_name: str, key: str, command: bool = False,
                     shift: bool = False) -> bool:
    return bool(_call("keystroke_in_app", app_name=app_name, key=key,
                      command=command, shift=shift, _timeout=25.0))


# ── Process control ──────────────────────────────────────────────────────

def kill_named(names: Iterable[str]) -> None:
    _call("kill_named", names=list(names), _timeout=25.0)


def kill_matching_command(needle: str) -> None:
    _call("kill_matching_command", needle=needle, _timeout=25.0)


# ── Audio / power / notifications ────────────────────────────────────────

def get_output_volume() -> int:
    return int(_call("get_output_volume"))


def is_output_muted() -> bool:
    return bool(_call("is_output_muted"))


def set_output_volume(level: int) -> None:
    _call("set_output_volume", level=int(level))


def set_output_muted(muted: bool) -> None:
    _call("set_output_muted", muted=bool(muted))


def system_power(mode: str) -> tuple[bool, str]:
    return _pair(_call("system_power", mode=mode, _timeout=35.0))


def notify(title: str, message: str) -> None:
    _call("notify", title=title, message=message)
