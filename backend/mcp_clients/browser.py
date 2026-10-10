"""Typed browser automation calls routed through the browser MCP server."""

from __future__ import annotations

from typing import Any

from . import registry


def command(action: str, params: dict[str, Any] | None = None,
            timeout: float = 120.0) -> dict[str, Any]:
    """Run a browser action via the audited browser MCP slot."""
    action = str(action or "").strip()
    if not action:
        return {"ok": False, "error": "Browser action is required."}
    result = registry.call(
        "browser",
        "browser_command",
        {"action": action, "params": params or {}, "timeout": float(timeout)},
        timeout=max(20.0, float(timeout) + 20.0),
    )
    return result if isinstance(result, dict) else {"ok": True, "message": str(result)}
