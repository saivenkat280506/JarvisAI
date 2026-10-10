"""
protocol.py — Minimal MCP stdio client (JSON-RPC 2.0, newline-delimited).
=======================================================================
Speaks the Model Context Protocol over a child process's stdin/stdout:

  -> {"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}
  <- {"jsonrpc":"2.0","id":1,"result":{...}}
  -> {"jsonrpc":"2.0","method":"notifications/initialized"}
  -> {"jsonrpc":"2.0","id":2,"method":"tools/call",
      "params":{"name":"run_command","arguments":{...}}}
  <- {"jsonrpc":"2.0","id":2,"result":{"content":[...],"isError":false}}

One persistent child per server slot; requests may be issued from any
thread (responses are matched by id). A dead child is respawned on the
next request; a request interrupted by a crash is retried once by the
registry after an explicit restart.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from queue import Empty, Queue
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "jarvis-backend", "version": "0.1.0"}
DEFAULT_TIMEOUT = 30.0


class MCPError(RuntimeError):
    """Transport- or tool-level failure.

    kind == "transport": the child died or the wire protocol failed —
        safe to restart the server and retry once.
    kind == "tool": the tool ran and reported an error — do not retry.
    """

    def __init__(self, message: str, *, kind: str = "transport", code: int | None = None,
                 data: Any = None):
        super().__init__(message)
        self.kind = kind
        self.code = code
        self.data = data


class StdioMCPServer:
    def __init__(self, name: str, cmd: list[str], *, cwd: str | None = None,
                 env: dict | None = None, stderr_path: str | None = None):
        self.name = name
        self.cmd = list(cmd)
        self.cwd = cwd
        self.env = env
        self.stderr_path = stderr_path
        # Called for every server→client JSON-RPC notification (id-less
        # message). Set by the registry to feed the terminal panel (§7.2).
        self.on_notification = None

        self._proc: subprocess.Popen | None = None
        self._stderr_file = None
        self._next_id = 0
        self._pending: dict[int, Queue] = {}
        self._pending_lock = threading.Lock()
        self._id_lock = threading.Lock()
        self._spawn_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._tools_cache: list[dict] | None = None

    # ── lifecycle ──────────────────────────────────────────────────────────

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        with self._spawn_lock:
            if self.is_alive():
                return
            stderr = subprocess.DEVNULL
            if self.stderr_path:
                self._stderr_file = open(self.stderr_path, "a", encoding="utf-8")
                stderr = self._stderr_file
            self._proc = subprocess.Popen(
                self.cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr,
                cwd=self.cwd,
                env=self.env,
                text=True,
                bufsize=1,
            )
            reader = threading.Thread(
                target=self._read_loop, args=(self._proc,),
                daemon=True, name=f"mcp-reader-{self.name}",
            )
            reader.start()
            self._handshake()

    def _handshake(self) -> None:
        self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
            timeout=15.0,
        )
        self.notify("notifications/initialized")

    def restart(self) -> None:
        self.stop()
        self.start()

    def stop(self) -> None:
        with self._spawn_lock:
            proc, self._proc = self._proc, None
            self._tools_cache = None
            if proc is None:
                return
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            if self._stderr_file:
                try:
                    self._stderr_file.close()
                except Exception:
                    pass
                self._stderr_file = None
            with self._pending_lock:
                pending = list(self._pending.values())
                self._pending.clear()
            for q in pending:
                q.put({"error": {"code": -32000, "message": f"{self.name}: stopped"}})

    # ── wire I/O ───────────────────────────────────────────────────────────

    def _read_loop(self, proc: subprocess.Popen) -> None:
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg_id = msg.get("id")
                if msg_id is None:
                    handler = self.on_notification
                    if handler is not None and "method" in msg:
                        try:
                            handler(msg)
                        except Exception:
                            pass  # a broken observer must not kill the reader
                    continue  # server notification
                if "method" in msg:
                    # Server-initiated request — we support none; decline.
                    self._send({"jsonrpc": "2.0", "id": msg_id,
                                "error": {"code": -32601, "message": "method not supported"}})
                    continue
                with self._pending_lock:
                    q = self._pending.get(msg_id)
                if q is not None:
                    q.put(msg)
        except Exception:
            pass
        finally:
            with self._pending_lock:
                pending = list(self._pending.values())
                self._pending.clear()
            for q in pending:
                q.put({"error": {"code": -32001, "message": f"{self.name}: server exited"}})

    def _send(self, payload: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise MCPError(f"{self.name}: not running", kind="transport")
        data = json.dumps(payload, ensure_ascii=False) + "\n"
        with self._write_lock:
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except Exception as exc:
                raise MCPError(f"{self.name}: write failed ({exc})", kind="transport") from exc

    def request(self, method: str, params: dict | None = None,
                timeout: float = DEFAULT_TIMEOUT) -> Any:
        if not self.is_alive():
            self.start()
        with self._id_lock:
            self._next_id += 1
            req_id = self._next_id
        q: Queue = Queue(maxsize=1)
        with self._pending_lock:
            self._pending[req_id] = q
        try:
            self._send({"jsonrpc": "2.0", "id": req_id, "method": method,
                        "params": params or {}})
            try:
                msg = q.get(timeout=timeout)
            except Empty:
                raise MCPError(
                    f"{self.name}: timeout after {timeout:.0f}s waiting for {method}",
                    kind="transport",
                ) from None
        finally:
            with self._pending_lock:
                self._pending.pop(req_id, None)
        if "error" in msg:
            err = msg["error"] or {}
            raise MCPError(
                f"{self.name}: {err.get('message', 'error')} (code {err.get('code')})",
                kind="transport",
                code=err.get("code"),
                data=err.get("data"),
            )
        return msg.get("result")

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # ── MCP surface ────────────────────────────────────────────────────────

    def list_tools(self, timeout: float = 15.0) -> list[dict]:
        if self._tools_cache is not None:
            return self._tools_cache
        result = self.request("tools/list", {}, timeout=timeout) or {}
        tools = result.get("tools") or []
        self._tools_cache = tools
        return tools

    def call_tool(self, name: str, arguments: dict | None = None,
                  timeout: float = DEFAULT_TIMEOUT) -> Any:
        result = self.request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout=timeout,
        ) or {}
        texts = [
            part.get("text", "")
            for part in result.get("content", [])
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        raw = "".join(texts)
        if result.get("isError"):
            raise MCPError(f"tool '{name}' failed: {raw or 'no details'}", kind="tool")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
