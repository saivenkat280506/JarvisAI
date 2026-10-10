"""
macos_server.py — MCP stdio server: AppleScript/system/GUI tools.

Run as `python -m mcp_servers.macos_server`. Two groups of tools:

  * macos_native actions — osascript, app/window control, clipboard,
    volume, power, notifications, process control (imported from
    mcp_servers.macos_native.py, the sole AppleScript/osascript surface);
  * gui_* tools — pyautogui mouse/keyboard/screen operations (pyautogui
    lives only in this process; backend code calls gui_* via
    mcp_clients/macos.py).

Talked to exclusively through mcp_clients; output volume and every
AppleScript action are audited in logs/mcp_audit.jsonl.
"""

from __future__ import annotations

import base64
import builtins
import io
import sys
from functools import partial

_original_print = print
builtins.print = partial(_original_print, file=sys.stderr)  # keep stdout protocol-clean

from . import macos_native as native  # noqa: E402
from ._server import serve  # noqa: E402

_gui = None


def gui():
    """Lazy pyautogui handle (imported only inside this server process)."""
    global _gui
    if _gui is None:
        import pyautogui
        pyautogui.FAILSAFE = False
        pyautogui.PAUSE = 0.05
        _gui = pyautogui
    return _gui


# ── native actions (kwargs-compatible signatures) ────────────────────────

_NATIVE_ACTIONS = [
    "osascript", "open_uri", "open_application", "close_application",
    "focus_application", "quit_google_chrome", "list_visible_windows",
    "get_work_area", "set_app_window_bounds", "window_bounds",
    "list_process_names", "pids_matching", "is_app_running",
    "accessibility_available", "set_clipboard", "get_clipboard",
    "keystroke_in_app", "kill_named", "kill_matching_command",
    "get_output_volume", "is_output_muted", "set_output_volume",
    "set_output_muted", "system_power", "notify",
]

TOOLS: dict[str, dict] = {name: {"fn": getattr(native, name)}
                          for name in _NATIVE_ACTIONS}


def run_open(args: list[str]):
    """Run macOS `open` with the given arguments (files, -a app, URIs)."""
    return native.run_open(*args)


TOOLS["run_open"] = {
    "fn": run_open,
    "schema": {"type": "object",
               "properties": {"args": {"type": "array", "items": {"type": "string"}}},
               "required": ["args"]},
}


# ── GUI tools (pyautogui) ────────────────────────────────────────────────

def gui_click(x: int, y: int) -> dict:
    gui().click(int(x), int(y))
    return {"x": int(x), "y": int(y)}


def gui_double_click(x: int, y: int) -> dict:
    gui().doubleClick(int(x), int(y))
    return {"x": int(x), "y": int(y)}


def gui_right_click(x: int, y: int) -> dict:
    gui().rightClick(int(x), int(y))
    return {"x": int(x), "y": int(y)}


def gui_move(x: int, y: int, duration: float = 0.15) -> dict:
    gui().moveTo(int(x), int(y), duration=float(duration))
    return {"x": int(x), "y": int(y)}


def gui_type(text: str, interval: float = 0.02) -> dict:
    gui().write(text, interval=max(0.0, float(interval)))
    return {"chars": len(text)}


def gui_press(key: str, presses: int = 1, interval: float = 0.05) -> dict:
    gui().press(key, presses=max(1, int(presses)), interval=max(0.0, float(interval)))
    return {"key": key}


def gui_hotkey(keys: list[str]) -> dict:
    gui().hotkey(*[str(k) for k in keys])
    return {"keys": [str(k) for k in keys]}


def gui_scroll(clicks: int, x: int | None = None, y: int | None = None) -> dict:
    if x is None or y is None:
        gui().scroll(int(clicks))
    else:
        gui().scroll(int(clicks), x=int(x), y=int(y))
    return {"clicks": int(clicks)}


def gui_screen_size() -> dict:
    width, height = gui().size()
    return {"width": int(width), "height": int(height)}


def gui_screenshot_region(left: int, top: int, width: int, height: int) -> str:
    """Base64 PNG of a screen region (mss CGDisplay capture — no
    screencapture CLI, which needs separate TCC approval)."""
    try:
        import mss
        from PIL import Image
        with mss.mss() as sct:
            raw = sct.grab({"left": int(left), "top": int(top),
                            "width": int(width), "height": int(height)})
            image = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    except Exception:
        image = gui().screenshot(region=(int(left), int(top), int(width), int(height)))
    image.thumbnail((1280, 720))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


for _name, _fn in [
    ("gui_click", gui_click), ("gui_double_click", gui_double_click),
    ("gui_right_click", gui_right_click), ("gui_move", gui_move),
    ("gui_type", gui_type), ("gui_press", gui_press),
    ("gui_hotkey", gui_hotkey), ("gui_scroll", gui_scroll),
    ("gui_screen_size", gui_screen_size),
    ("gui_screenshot_region", gui_screenshot_region),
]:
    TOOLS[_name] = {"fn": _fn}


if __name__ == "__main__":
    serve("macos", TOOLS)
