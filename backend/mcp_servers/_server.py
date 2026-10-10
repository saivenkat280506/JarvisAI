"""
_server.py — Shared MCP stdio server loop (newline-delimited JSON-RPC 2.0).

A server module builds a {tool_name: {"fn", "description"}} table and calls
serve(). The loop:

  * patches builtins.print -> stderr at startup so stray prints from native
    libraries never corrupt the JSON-RPC stream on stdout;
  * answers initialize / tools/list / ping inline;
  * dispatches tools/call on a worker thread (a long-running run_command
    must not head-of-line-block other tool calls);
  * encodes return values as JSON (tuples/lists, sets, CompletedProcess).
"""

from __future__ import annotations

import builtins
import inspect
import json
import subprocess
import sys
import threading
import typing
from functools import partial
from typing import Any, get_args, get_origin

PROTOCOL_VERSION = "2025-06-18"
SERVER_VERSION = "0.1.0"

_PRIM_TO_JSON = {
    str: "string", int: "integer", float: "number", bool: "boolean",
    dict: "object", list: "array", tuple: "array", set: "array",
    frozenset: "array", type(None): "null",
}


_stdout_lock = threading.Lock()


def _write(obj: dict) -> None:
    """Serialize JSON-RPC frames to stdout (worker threads reply while the
    main loop reads stdin — every write must take the lock)."""
    data = json.dumps(obj, ensure_ascii=False) + "\n"
    with _stdout_lock:
        sys.stdout.write(data)
        sys.stdout.flush()


def notify(method: str, params: dict | None = None) -> None:
    """Send a JSON-RPC notification (no id) to the client from any thread.

    Used by tools to stream long-running output (shell stdout/stderr lines)
    to the frontend terminal panel while the tool call is still in flight
    (master prompt §7.2: never buffer output to completion).
    """
    _write({"jsonrpc": "2.0", "method": method, "params": params or {}})


def encode(value: Any) -> Any:
    """Make a tool return value JSON-serializable (client rebuilds shapes)."""
    if isinstance(value, subprocess.CompletedProcess):
        args = value.args
        return {
            "returncode": value.returncode,
            "stdout": value.stdout if isinstance(value.stdout, (str, type(None))) else repr(value.stdout),
            "stderr": value.stderr if isinstance(value.stderr, (str, type(None))) else repr(value.stderr),
            "args": list(args) if isinstance(args, (list, tuple)) else str(args),
            "_completed": True,
        }
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    if isinstance(value, tuple):
        return [encode(v) for v in value]
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _json_type(annotation: Any) -> str:
    if annotation in (inspect.Parameter.empty, None, "", Any):
        return "string"
    if isinstance(annotation, str):
        annotation = annotation.strip()
        simple = {"str": "string", "int": "integer", "float": "number",
                  "bool": "boolean", "dict": "object", "list": "array"}
        if annotation in simple:
            return simple[annotation]
        if annotation.endswith("]") or annotation.startswith("list") or "Iterable" in annotation:
            return "array"
        if annotation.startswith("dict"):
            return "object"
        if annotation.startswith("Optional"):
            inner = annotation[len("Optional["):-1]
            return _json_type(inner)
        return "string"
    direct = _PRIM_TO_JSON.get(annotation)
    if direct:
        return direct
    origin = get_origin(annotation)
    if origin in (list, tuple, set, frozenset):
        return "array"
    if origin is dict:
        return "object"
    args = get_args(annotation)
    if args:
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            return _json_type(non_none[0])
    if "Iterable" in getattr(annotation, "__name__", str(annotation)):
        return "array"
    return "string"


def schema_for(fn: Any) -> dict:
    """Generate a minimal JSON schema for a Python function signature."""
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}
    props: dict[str, dict] = {}
    required: list[str] = []
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return {"type": "object", "properties": {}}
    for pname, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        ann = hints.get(pname, param.annotation)
        prop: dict[str, Any] = {"type": _json_type(ann)}
        if param.default is inspect.Parameter.empty:
            required.append(pname)
        else:
            try:
                json.dumps(param.default)
                prop["default"] = param.default
            except (TypeError, ValueError):
                pass
        props[pname] = prop
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def _first_doc_line(fn: Any) -> str:
    doc = inspect.getdoc(fn) or ""
    return doc.splitlines()[0] if doc else ""


def serve(server_name: str, tools: dict[str, dict]) -> None:
    """Run the stdio event loop until stdin closes.

    tools: {name: {"fn": callable, "description"?, "schema"?}}
    """
    builtins.print = partial(print, file=sys.stderr)  # keep stdout protocol-clean

    def send(obj: dict) -> None:
        _write(obj)

    def reply(req_id: Any, result: Any) -> None:
        send({"jsonrpc": "2.0", "id": req_id, "result": result})

    def reply_error(req_id: Any, code: int, message: str) -> None:
        send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})

    def handle_call(req_id: Any, params: dict) -> None:
        name = (params or {}).get("name") or ""
        arguments = (params or {}).get("arguments") or {}
        tool = tools.get(name)
        if tool is None:
            text = f"unknown tool '{name}'"
            reply(req_id, {"content": [{"type": "text", "text": text}], "isError": True})
            return
        try:
            value = tool["fn"](**arguments)
            text = json.dumps(encode(value), ensure_ascii=False)
            reply(req_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except Exception as exc:
            text = f"{type(exc).__name__}: {exc}"
            reply(req_id, {"content": [{"type": "text", "text": text}], "isError": True})

    tool_list = None  # built lazily, cached

    def build_tool_list() -> list[dict]:
        nonlocal tool_list
        if tool_list is None:
            tool_list = [
                {
                    "name": name,
                    "description": spec.get("description") or _first_doc_line(spec["fn"]),
                    "inputSchema": spec.get("schema") or schema_for(spec["fn"]),
                }
                for name, spec in tools.items()
            ]
        return tool_list

    print(f"[mcp:{server_name}] ready, {len(tools)} tools", file=sys.stderr)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        req_id = msg.get("id")
        method = msg.get("method")
        if req_id is None or method is None:
            continue  # notification (notifications/initialized, cancelled, …)
        params = msg.get("params") or {}
        if method == "initialize":
            reply(req_id, {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": server_name, "version": SERVER_VERSION},
            })
        elif method == "tools/list":
            reply(req_id, {"tools": build_tool_list()})
        elif method == "tools/call":
            threading.Thread(target=handle_call, args=(req_id, params),
                             daemon=True).start()
        elif method == "ping":
            reply(req_id, {})
        else:
            reply_error(req_id, -32601, f"method not found: {method}")

    sys.exit(0)
