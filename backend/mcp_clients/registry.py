"""
registry.py — MCP server slot registry, audit log, and the one public
entry point backend code uses: mcp_clients.call(slot, tool, args).

Slots:
  shell — command execution, file ops, process control (backend/mcp_servers/shell_server.py)
  macos — AppleScript + GUI (pyautogui) actions     (backend/mcp_servers/macos_server.py)
  browser — allowlisted browser automation           (backend/mcp_servers/browser_server.py)

Local servers are spawned as `python -m mcp_servers.<name>_server` from the
backend directory. Externally hosted MCP servers (e.g. Desktop Commander) can
replace a slot via JARVIS_MCP_SHELL_CMD / JARVIS_MCP_MACOS_CMD (shell-split).

Every tool call is appended to logs/mcp_audit.jsonl with truncated args,
latency, and outcome — the single audit trail for OS-level actions.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import threading
import time
from pathlib import Path

from .protocol import DEFAULT_TIMEOUT, MCPError, StdioMCPServer

BACKEND_DIR = str(Path(__file__).resolve().parents[1])
LOGS_DIR = Path(BACKEND_DIR) / "logs"
AUDIT_PATH = LOGS_DIR / "mcp_audit.jsonl"

_AUDIT_LOCK = threading.Lock()
_CLIENTS: dict[str, StdioMCPServer] = {}
_CLIENTS_LOCK = threading.Lock()
_shutting_down = False

_notification_handler = None  # fn(msg: dict) — see set_notification_handler


def set_notification_handler(handler) -> None:
    """Register the observer for server→client JSON-RPC notifications.

    services.terminal_stream installs itself here so raw shell output
    streams to the frontend terminal panel while a tool call is still
    running (master prompt §7.2 / §0.8).
    """
    global _notification_handler
    _notification_handler = handler


def _dispatch_notification(msg: dict) -> None:
    handler = _notification_handler
    if handler is None:
        return
    try:
        handler(msg)
    except Exception:
        pass  # a broken observer must never break tool execution


def _local_cmd(module: str) -> list[str]:
    return [sys.executable, "-m", f"mcp_servers.{module}"]


_SLOTS: dict[str, dict] = {
    "shell": {
        "cmd": _local_cmd("shell_server"),
        "cmd_env": "JARVIS_MCP_SHELL_CMD",
        "err_log": "mcp_shell.err.log",
    },
    "macos": {
      "cmd": _local_cmd("macos_server"),
      "cmd_env": "JARVIS_MCP_MACOS_CMD",
      "err_log": "mcp_macos.err.log",
    },
    "browser": {
      "cmd": _local_cmd("browser_server"),
      "cmd_env": "JARVIS_MCP_BROWSER_CMD",
      "err_log": "mcp_browser.err.log",
    },
}


def get_client(slot: str) -> StdioMCPServer:
    """Return (and lazily create) the client for a registered slot."""
    if slot not in _SLOTS:
        raise MCPError(f"unknown MCP slot '{slot}' (known: {', '.join(_SLOTS)})",
                       kind="tool")
    if _shutting_down:
        raise MCPError(f"{slot}: backend is shutting down", kind="tool")
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(slot)
        if client is None:
            spec = _SLOTS[slot]
            cmd = spec["cmd"]
            env_override = os.environ.get(spec["cmd_env"], "").strip()
            if env_override:
                cmd = shlex.split(env_override)
            env = dict(os.environ)
            env["PYTHONUNBUFFERED"] = "1"
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            client = StdioMCPServer(
                slot,
                cmd,
                cwd=BACKEND_DIR,
                env=env,
                stderr_path=str(LOGS_DIR / spec["err_log"]),
            )
            client.on_notification = _dispatch_notification
            _CLIENTS[slot] = client
        return client


def _audit(slot: str, tool: str, args: dict, *, ok: bool, ms: float,
           attempt: int, error: str | None) -> None:
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "slot": slot,
        "tool": tool,
        "args": {k: (str(v)[:400]) for k, v in (args or {}).items()},
        "attempt": attempt,
        "ok": ok,
        "ms": round(ms, 1),
    }
    if error:
        record["error"] = error[:400]
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with _AUDIT_LOCK:
            with open(AUDIT_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # audit must never break action execution


def call(slot: str, tool: str, args: dict | None = None,
         timeout: float = DEFAULT_TIMEOUT) -> object:
    """Invoke an MCP tool. Restarts a dead server once and retries only
    transport failures (tool-reported errors are never retried)."""
    if _shutting_down:
        raise MCPError(f"{slot}: backend is shutting down", kind="tool")
    client = get_client(slot)
    args = args or {}

    def _emit_start() -> None:
        try:
            from services.terminal_stream import emit_tool_start
            emit_tool_start(slot, tool, args)
        except Exception:
            pass

    def _emit_exit(*, ok: bool, result: object = None,
                   error: str | None = None, t0: float = 0.0) -> None:
        try:
            from services.terminal_stream import emit_tool_exit
            returncode = result.get("returncode") if isinstance(result, dict) else None
            emit_tool_exit(slot, tool, args, ok=ok, returncode=returncode,
                           error=error, ms=(time.monotonic() - t0) * 1000)
        except Exception:
            pass

    _emit_start()
    last_exc: MCPError | None = None
    for attempt in (1, 2):
        if _shutting_down:
            raise MCPError(f"{slot}: backend is shutting down", kind="tool")
        t0 = time.monotonic()
        try:
            if not client.is_alive():
                client.start()
            result = client.call_tool(tool, args, timeout=timeout)
            action_ok = not (isinstance(result, dict) and result.get("ok") is False)
            action_error = None
            if not action_ok:
                action_error = str(result.get("error") or result.get("message") or "tool returned ok=false")
            _audit(slot, tool, args, ok=action_ok,
                   ms=(time.monotonic() - t0) * 1000, attempt=attempt,
                   error=action_error)
            _emit_exit(ok=action_ok, result=result, error=action_error, t0=t0)
            return result
        except MCPError as exc:
            last_exc = exc
            _audit(slot, tool, args, ok=False,
                   ms=(time.monotonic() - t0) * 1000, attempt=attempt,
                   error=str(exc))
            if exc.kind == "tool" or attempt == 2 or _shutting_down:
                _emit_exit(ok=False, error=str(exc), t0=t0)
                raise
            try:
                if not _shutting_down:
                    try:
                        from services.terminal_stream import emit_tool_note
                        emit_tool_note(slot, tool, args,
                                       f"attempt failed ({exc}) — restarting {slot} MCP server")
                    except Exception:
                        pass
                    client.restart()
            except Exception:
                pass
    raise last_exc if last_exc else MCPError(f"{slot}: {tool} failed", kind="transport")


def list_tools(slot: str) -> list[dict]:
    return get_client(slot).list_tools()


def shutdown_all() -> None:
    """Stop every spawned MCP server child (call from services/shutdown).

    Sets _shutting_down first so a concurrent background thread (e.g. the
    process monitor) cannot re-create a client and leak a fresh child."""
    global _shutting_down
    _shutting_down = True
    with _CLIENTS_LOCK:
        clients = list(_CLIENTS.values())
        _CLIENTS.clear()
    for client in clients:
        try:
            client.stop()
        except Exception:
            pass


import atexit  # noqa: E402  (registered after definition so import order is safe)

atexit.register(shutdown_all)
