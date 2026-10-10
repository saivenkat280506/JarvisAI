"""Terminal transparency tests (master prompt §7 / §0.8).

Covers: line-by-line shell streaming, unchanged run_command contract,
registry start/exit emission, tool_call_log persistence, and observer
failure isolation. No FastAPI loop — terminal_stream uses a test sink.
"""
import os
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmpdir = tempfile.mkdtemp(prefix="jarvis-term-test-")
os.environ["JARVIS_DB_PATH"] = os.path.join(_tmpdir, "test.db")

from mcp_clients import registry  # noqa: E402
from mcp_servers import shell_server  # noqa: E402
from services import terminal_stream  # noqa: E402


def check(label, cond):
    if not cond:
        raise AssertionError(label)
    print("OK", label)


def main():
    # ── 1. server-side line streaming keeps the old contract ──────────────
    events = []
    original_notify = shell_server.notify
    shell_server.notify = lambda method, params=None: events.append(
        {"method": method, "params": params or {}})
    try:
        result = shell_server.run_command(
            "python3 -u -c \"import sys; print('hello-stdout'); "
            "print('hello-stderr', file=sys.stderr); sys.exit(3)\"")

        check("run_command keeps return shape",
              set(result.keys()) == {"returncode", "stdout", "stderr", "timed_out"})
        check("returncode preserved", result["returncode"] == 3)
        check("stdout captured", "hello-stdout" in result["stdout"])
        check("stderr captured", "hello-stderr" in result["stderr"])
        check("not timed out", result["timed_out"] is False)
        check("line events emitted while running", len(events) >= 2)
        check("events are jarvis/terminal_output",
              all(e["method"] == "jarvis/terminal_output" for e in events))
        streams = [e["params"]["stream"] for e in events]
        check("stdout and stderr tagged",
              "stdout" in streams and "stderr" in streams)
        lines = [e["params"]["line"] for e in events]
        check("raw lines streamed",
              "hello-stdout" in lines and "hello-stderr" in lines)
        check("every event carries tool + command",
              all(e["params"].get("tool") == "run_command"
                  and isinstance(e["params"].get("command"), str)
                  for e in events))

        # timeout contract unchanged (kill + returncode -1 + message)
        events.clear()
        t0 = time.monotonic()
        timed = shell_server.run_command("sleep 5", timeout=1.0)
        elapsed = time.monotonic() - t0
        check("timeout flag set", timed["timed_out"] is True)
        check("timeout returncode -1", timed["returncode"] == -1)
        check("timeout stderr message", "timed out after 1s" in timed["stderr"])
        check("timeout is prompt", elapsed < 4.0)
        check("timeout line streamed",
              any("timed out" in e["params"]["line"] for e in events))

        # streaming must not corrupt empty-output commands
        empty = shell_server.run_command("true")
        check("empty command contract",
              empty == {"returncode": 0, "stdout": "", "stderr": "",
                        "timed_out": False})
    finally:
        shell_server.notify = original_notify

    # ── 2. registry → terminal_stream → WS payload flow ───────────────────
    sink: list[dict] = []
    terminal_stream.set_test_sink(sink)
    notes: list[dict] = []

    def observe(msg):
        notes.append(msg)
        terminal_stream._on_notification(msg)
        raise RuntimeError("observer failures must never break tool calls")

    registry.set_notification_handler(observe)
    try:
        out = registry.call("shell", "run_command",
                            {"command": "echo registry-line", "cwd": None,
                             "timeout": 10.0, "env": None})
        check("call succeeds despite raising observer",
              out["returncode"] == 0 and "registry-line" in out["stdout"])
    finally:
        registry.set_notification_handler(None)

    check("observer saw terminal notifications",
          any(n.get("method") == "jarvis/terminal_output" for n in notes))
    kinds = [e["type"] for e in sink]
    check("start emitted first", bool(kinds) and kinds[0] == "terminal_output")
    check("start is the cmd header",
          sink[0].get("stream") == "cmd"
          and sink[0].get("line") == "echo registry-line")
    cmd_idx = 0
    line_idx = next((i for i, e in enumerate(sink)
                     if e["type"] == "terminal_output"
                     and e.get("stream") == "stdout"
                     and "registry-line" in e.get("line", "")), -1)
    exit_idx = next((i for i, e in enumerate(sink)
                     if e["type"] == "terminal_exit"), -1)
    check("raw stdout line present", line_idx != -1)
    check("exit emitted last", exit_idx == len(sink) - 1)
    check("ordering cmd < line < exit",
          0 <= cmd_idx < line_idx < exit_idx)
    exit_event = sink[exit_idx]
    check("exit carries returncode 0 + ok",
          exit_event.get("returncode") == 0 and exit_event.get("ok") is True)
    check("exit carries slot/tool",
          exit_event.get("slot") == "shell"
          and exit_event.get("tool") == "run_command")
    check("cmd event tagged slot/tool",
          sink[0].get("slot") == "shell"
          and sink[0].get("tool") == "run_command")

    # ── 3. scrollback persisted to tool_call_log (§7.3) ───────────────────
    conn = sqlite3.connect(os.environ["JARVIS_DB_PATH"])
    rows = conn.execute(
        "SELECT tool_name, success, latency_ms FROM tool_call_log "
        "WHERE intent = 'mcp'").fetchall()
    conn.close()
    check("tool_call_log row persisted",
          any(r[0] == "shell.run_command" and r[1] == 1 for r in rows))

    # ── 4. failure path emits a failing exit ──────────────────────────────
    sink.clear()
    try:
        registry.call("shell", "read_file",
                      {"path": "/nonexistent/jarvis-term-test-file",
                       "max_bytes": 1024})
        tool_failed = False
    except registry.MCPError:
        tool_failed = True
    check("missing file raises tool error", tool_failed)
    failure_exits = [e for e in sink if e["type"] == "terminal_exit"]
    check("failing exit emitted", len(failure_exits) == 1)
    check("failing exit flagged", failure_exits[0].get("ok") is False)

    terminal_stream.set_test_sink(None)
    registry.shutdown_all()
    print("ALL OK")


if __name__ == "__main__":
    main()
