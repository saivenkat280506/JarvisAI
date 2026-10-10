"""
terminal_stream.py — Bridge from the MCP tool layer to the frontend
in-app terminal panel (master prompt §7 / §0.8).

Three sources feed one panel:

  1. registry start/exit  — every MCP tool call on every slot, so no tier
     ever executes hidden (shell / macos / browser alike);
  2. shell-server line notifications — raw stdout/stderr pumped line by
     line *while* the command runs, not after completion (§7.2);
  3. tool notes           — retry/restart status lines.

Everything is broadcast on the app WebSocket as `terminal_output` /
`terminal_exit` events and exit rows are persisted to `tool_call_log`
so scrollback stays queryable per session (§7.3).

Thread model: emissions arrive from MCP reader threads and tool-caller
threads; broadcasts are marshalled onto the main event loop captured at
startup. A test sink can be installed instead of a loop for unit tests.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from services.websocket_manager import manager

_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_installed = False
_test_sink: list[dict] | None = None

_MAX_ARGS = 160


def install() -> None:
    """Capture the running event loop and subscribe to MCP notifications.

    Called once from services.startup inside the FastAPI lifespan (so the
    loop exists). Safe to call again — later loops replace earlier ones.
    """
    global _loop, _installed
    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        _loop = None
    try:
        from mcp_clients.registry import set_notification_handler
        set_notification_handler(_on_notification)
    except Exception:
        pass
    _installed = True


def set_test_sink(sink: list[dict] | None) -> None:
    """Capture emitted events into a list instead of broadcasting (tests)."""
    global _test_sink
    _test_sink = sink


# ── broadcast plumbing ──────────────────────────────────────────────────────

def _broadcast(payload: dict) -> None:
    with _lock:
        if _test_sink is not None:
            _test_sink.append(payload)
            return
        loop = _loop
    if loop is None or loop.is_closed():
        return
    try:
        asyncio.run_coroutine_threadsafe(manager.broadcast_json(payload), loop)
    except Exception:
        pass  # transparency must never break the tool that triggered it


def _persist_call(slot: str, tool: str, args: dict, *, ok: bool,
                  ms: float, error: str | None) -> None:
    """Mirror the exit into tool_call_log for §7.3 scrollback queries."""
    try:
        from memory.db import current_conversation_id, log_tool_call
        started = datetime.fromtimestamp(
            time.time() - ms / 1000.0, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        log_tool_call(
            conversation_id=current_conversation_id(),
            intent="mcp",
            tool_name=f"{slot}.{tool}",
            input_data=args if isinstance(args, dict) else {},
            output_text=(error or "")[:500],
            success=ok,
            started_at=started,
            latency_ms=ms,
        )
    except Exception:
        pass  # logging must never fail the tool call


# ── rendering ───────────────────────────────────────────────────────────────

def _args_summary(slot: str, tool: str, args: dict) -> str:
    if not isinstance(args, dict):
        args = {}
    if tool == "run_command" and isinstance(args.get("command"), str):
        return args["command"]
    if tool == "run_command" and isinstance(args.get("cmd"), str):
        return args["cmd"]
    if tool in ("open_app", "open_application"):
        app = args.get("app") or args.get("app_name") or args.get("name") or "Application"
        return f'open -a "{app}"'
    if tool in ("read_headlines", "read_news_headlines", "news"):
        q = args.get("query") or args.get("topic") or "world news"
        return f'curl -s "news://api/v1/headlines?q={q}"'
    if tool in ("download", "download_file"):
        url = args.get("url") or args.get("link") or ""
        return f'curl -LO "{url}"'
    if tool in ("smart_search", "web_search", "search_browser", "search_google"):
        q = args.get("query") or args.get("q") or ""
        return f'curl -s "https://search.brave.com/search?q={q}"'
    if tool == "search_files":
        return f'mdfind "{args.get("query", "")}"'
    if tool in ("write_file", "create_file"):
        p = args.get("file_name") or args.get("path") or args.get("name") or "file.txt"
        return f'cat << \'EOF\' > "{p}"'
    if tool in ("make_dirs", "create_folder"):
        p = args.get("folder_name") or args.get("directory_name") or args.get("path") or args.get("name") or "folder"
        return f'mkdir -p "{p}"'
    if tool == "send_whatsapp":
        name = args.get("name") or args.get("contact") or "contact"
        return f'whatsapp-cli send --to "{name}"'
    if tool in ("play_spotify", "play_youtube_music", "play_youtube_search"):
        q = args.get("song") or args.get("query") or "music"
        return f'music-play --query "{q}"'
    if tool == "calculate":
        left = args.get("left", "")
        op = args.get("operator", "+")
        right = args.get("right", "")
        return f'python3 -c "print({left} {op} {right})"'
    if tool in ("remember", "recall"):
        k = args.get("key") or args.get("text") or ""
        return f'jarvis-memory --key "{k}"'
    if tool in ("add_task", "list_tasks", "complete_task"):
        return f'jarvis-task {tool.replace("_task", "")}'
    if tool == "volume_control":
        level = args.get("level") or args.get("volume") or "50"
        return f'osascript -e "set volume output volume {level}"'
    if tool == "open_path":
        return f'open "{args.get("target", "")}"'
    if tool == "spawn_background" and isinstance(args.get("command"), str):
        return args["command"]
    parts = []
    for key, value in (args or {}).items():
        text = str(value).replace("\n", " ")
        if len(text) > 60:
            text = text[:57] + "…"
        parts.append(f"{key}={text}")
    joined = " ".join(parts)
    if len(joined) > _MAX_ARGS:
        joined = joined[:_MAX_ARGS - 1] + "…"
    return f"{slot}.{tool}" + (f" {joined}" if joined else "")


def emit_tool_line(line: str, stream: str = "stdout", tool: str = "shell") -> None:
    """Broadcast an output line for active tool execution."""
    _broadcast({
        "type": "terminal_output",
        "stream": stream,
        "tool": tool,
        "line": line,
        "ts": time.time(),
    })


# ── emitters called from mcp_clients.registry ───────────────────────────────

def emit_tool_start(slot: str, tool: str, args: dict) -> None:
    """Header line — emitted *before* the request leaves the client, so the
    command is visible from the moment execution begins (§0.8)."""
    command = _args_summary(slot, tool, args)
    _broadcast({
        "type": "terminal_output",
        "stream": "cmd",
        "slot": slot,
        "tool": tool,
        "command": command,
        "line": command,
        "ts": time.time(),
    })


def emit_tool_note(slot: str, tool: str, args: dict, line: str) -> None:
    """Status line (e.g. MCP server restart on a transport failure)."""
    _broadcast({
        "type": "terminal_output",
        "stream": "note",
        "slot": slot,
        "tool": tool,
        "line": line,
        "ts": time.time(),
    })


def emit_tool_exit(slot: str, tool: str, args: dict, *, ok: bool,
                   returncode: int | None = None, error: str | None = None,
                   ms: float = 0.0) -> None:
    command = _args_summary(slot, tool, args)
    _broadcast({
        "type": "terminal_exit",
        "slot": slot,
        "tool": tool,
        "command": command,
        "returncode": returncode,
        "ok": bool(ok),
        "error": (error[:300] if error else None),
        "ms": round(ms, 1),
        "ts": time.time(),
    })
    _persist_call(slot, tool, args, ok=ok, ms=ms, error=error)


# ── notification handler (line-by-line shell output) ────────────────────────

def _on_notification(msg: dict) -> None:
    """Handle JSON-RPC notifications from an MCP server child process."""
    method = str(msg.get("method") or "")
    if not method.startswith("jarvis/terminal"):
        return
    params = msg.get("params") or {}
    if not isinstance(params, dict):
        return
    if method == "jarvis/terminal_output":
        line = params.get("line")
        if not isinstance(line, str):
            return
        _broadcast({
            "type": "terminal_output",
            "stream": str(params.get("stream") or "stdout"),
            "slot": "shell",
            "tool": str(params.get("tool") or "run_command"),
            "command": str(params.get("command") or ""),
            "line": line,
            "ts": time.time(),
        })
