"""
macos.py — Typed wrappers for GUI / clipboard tools on the `macos` MCP slot.

os_agent.py and automation.py use these instead of importing pyautogui or
pyperclip directly. System-control wrappers (volume, power, AppleScript…)
are proxied by the executor/sys_platform.py facade via registry.call().
"""

from __future__ import annotations

from typing import Any

from . import registry


def set_clipboard(text: str) -> bool:
    return registry.call("macos", "set_clipboard", {"text": text})


def get_clipboard() -> str:
    return registry.call("macos", "get_clipboard", {})


def open_uri(uri: str) -> bool:
    return registry.call("macos", "open_uri", {"uri": uri})


def gui_click(x: int, y: int) -> Any:
    return registry.call("macos", "gui_click", {"x": int(x), "y": int(y)})


def gui_double_click(x: int, y: int) -> Any:
    return registry.call("macos", "gui_double_click", {"x": int(x), "y": int(y)})


def gui_right_click(x: int, y: int) -> Any:
    return registry.call("macos", "gui_right_click", {"x": int(x), "y": int(y)})


def gui_move(x: int, y: int, duration: float = 0.15) -> Any:
    return registry.call("macos", "gui_move",
                         {"x": int(x), "y": int(y), "duration": duration})


def gui_type(text: str, interval: float = 0.02) -> Any:
    return registry.call("macos", "gui_type", {"text": text, "interval": interval})


def gui_press(key: str, presses: int = 1, interval: float = 0.05) -> Any:
    return registry.call("macos", "gui_press",
                         {"key": key, "presses": presses, "interval": interval})


def gui_hotkey(keys: list[str]) -> Any:
    return registry.call("macos", "gui_hotkey", {"keys": list(keys)})


def gui_scroll(clicks: int, x: int | None = None, y: int | None = None) -> Any:
    return registry.call("macos", "gui_scroll",
                         {"clicks": int(clicks), "x": x, "y": y})


def gui_screen_size() -> dict[str, Any]:
    return registry.call("macos", "gui_screen_size", {})


def gui_screenshot_region(left: int, top: int, width: int, height: int) -> str:
    """Base64-encoded PNG of a screen region."""
    return registry.call("macos", "gui_screenshot_region",
                         {"left": int(left), "top": int(top),
                          "width": int(width), "height": int(height)})
