"""MCP server for browser actions.

Browser commands enter through the same audited MCP registry as shell and
macOS actions. The Puppeteer control process remains a private implementation
detail hosted behind this server.
"""

from __future__ import annotations

import builtins
import sys
from functools import partial
from typing import Any

_original_print = print
builtins.print = partial(_original_print, file=sys.stderr)

from ._server import serve  # noqa: E402

_ALLOWED_ACTIONS = frozenset({
    "status", "open", "navigate", "new_tab", "click", "click_text",
    "type", "press", "scroll", "wait", "wait_for", "screenshot",
    "info", "snapshot", "set_window_bounds", "window_bounds",
    "youtube_play", "play_youtube", "youtube_music_play",
    "spotify_login", "spotify_search", "spotify_status", "scroll_test",
    "web_search", "search_and_scroll", "google_search", "search_brief",
})
_WHATSAPP_HOSTS = frozenset({"whatsapp.com", "web.whatsapp.com"})
_MUTATING_ACTIONS = frozenset({"click", "click_text", "type", "press"})


def _is_whatsapp_url(value: Any) -> bool:
    from urllib.parse import urlparse

    host = (urlparse(str(value or "")).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain)
               for domain in _WHATSAPP_HOSTS)


def browser_command(action: str, params: dict[str, Any] | None = None,
                    timeout: float = 120.0) -> dict[str, Any]:
    """Call an allowlisted Puppeteer action from the browser MCP process."""
    action = str(action or "").strip()
    if action not in _ALLOWED_ACTIONS:
        return {"ok": False, "error": f"Browser action '{action}' is not available."}

    from executor.puppeteer_client import command

    try:
        if action in _MUTATING_ACTIONS:
            page_info = command("info", timeout=10.0)
            if _is_whatsapp_url((page_info or {}).get("url")):
                return {
                    "ok": False,
                    "error": "Browser edits on WhatsApp are disabled. Use the confirmation-gated WhatsApp tool.",
                }
        result = command(action, timeout=max(1.0, min(float(timeout or 120.0), 300.0)),
                         **(params or {}))
        if (result or {}).get("ok") is not False and action in {
            "open", "navigate", "new_tab", "click", "click_text", "type",
            "press", "web_search", "search_and_scroll", "google_search",
            "search_brief", "youtube_play", "play_youtube", "youtube_music_play",
        }:
            try:
                from executor.sys_platform import focus_application
                focus_application("Google Chrome")
            except Exception:
                pass
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


TOOLS = {"browser_command": {"fn": browser_command}}


if __name__ == "__main__":
    serve("browser", TOOLS)
