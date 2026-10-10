"""
shell_server.py — MCP stdio server: shell / file / process tools.

The only command-execution surface of the backend. Run as
`python -m mcp_servers.shell_server`; talked to exclusively via
mcp_clients/shell.py.
"""

from __future__ import annotations

import base64
import builtins
import os
import signal
import subprocess
import sys
import threading
from functools import partial

_original_print = print
builtins.print = partial(_original_print, file=sys.stderr)  # keep stdout protocol-clean

from ._server import notify, serve  # noqa: E402


def _emit_line(tool: str, command: str, stream: str, line: str) -> None:
    """Push one output line to the client as it is produced (§7.2)."""
    try:
        notify("jarvis/terminal_output",
               {"tool": tool, "command": command, "stream": stream, "line": line})
    except Exception:
        pass  # streaming must never break command execution


def _run_streaming(command, *, cwd: str | None = None, env: dict | None = None,
                   timeout: float = 30.0, tool: str = "run_command",
                   shell: bool = True) -> tuple[int, str, str, bool]:
    """Execute a command, notifying each output line as it arrives.

    Returns (returncode, stdout, stderr, timed_out). On timeout the child is
    killed and returncode is -1 — the same contract subprocess.run +
    TimeoutExpired produced here before streaming was added.
    """
    timeout = max(1.0, min(float(timeout or 30.0), 600.0))
    full_env = dict(os.environ)
    if env:
        full_env.update({str(k): str(v) for k, v in env.items()})
    display = command if isinstance(command, str) else " ".join(map(str, command))
    try:
        proc = subprocess.Popen(
            command, shell=shell, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
            cwd=cwd, env=full_env,
        )
    except Exception as exc:
        return 127, "", f"{type(exc).__name__}: {exc}", False

    out_chunks: list[str] = []
    err_chunks: list[str] = []

    def pump(pipe, buf: list[str], tag: str) -> None:
        try:
            for line in pipe:
                buf.append(line)
                _emit_line(tool, display, tag, line.rstrip("\n"))
        except Exception:
            pass
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    readers = [
        threading.Thread(target=pump, args=(proc.stdout, out_chunks, "stdout"), daemon=True),
        threading.Thread(target=pump, args=(proc.stderr, err_chunks, "stderr"), daemon=True),
    ]
    for reader in readers:
        reader.start()

    timed_out = False
    try:
        returncode = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            proc.kill()
        except Exception:
            pass
        returncode = proc.wait()
        returncode = -1
    for reader in readers:
        reader.join(timeout=5.0)
    if timed_out:
        _emit_line(tool, display, "stderr", f"timed out after {timeout:g}s")
    return returncode, "".join(out_chunks), "".join(err_chunks), timed_out


def run_command(command: str, cwd: str | None = None, timeout: float = 30.0,
                env: dict | None = None) -> dict:
    """Run a shell command; never raises for non-zero exits."""
    timeout = max(1.0, min(float(timeout or 30.0), 600.0))
    returncode, stdout, stderr, timed_out = _run_streaming(
        command, cwd=cwd, env=env, timeout=timeout, tool="run_command")
    if timed_out and not stderr:
        stderr = f"timed out after {timeout:g}s"
    return {"returncode": returncode, "stdout": stdout,
            "stderr": stderr, "timed_out": timed_out}


def read_file(path: str, max_bytes: int = 2_000_000) -> str:
    max_bytes = max(1, min(int(max_bytes), 20_000_000))
    with open(path, "rb") as fh:
        data = fh.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    return data[:max_bytes].decode("utf-8", errors="replace")


def write_file(path: str, content: str, append: bool = False) -> dict:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    mode = "a" if append else "w"
    with open(path, mode, encoding="utf-8") as fh:
        written = fh.write(content)
    return {"path": path, "bytes": written, "appended": bool(append)}


def write_binary_file(path: str, content_base64: str, append: bool = False) -> dict:
    """Write base64-encoded bytes through the shell MCP boundary."""
    data = base64.b64decode(content_base64, validate=True)
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    mode = "ab" if append else "wb"
    with open(path, mode) as fh:
        written = fh.write(data)
    return {"path": path, "bytes": written, "appended": bool(append)}


def list_dir(path: str) -> list[str]:
    return sorted(os.listdir(path))


def file_exists(path: str) -> bool:
    return os.path.exists(path)


def search_files(query: str, directory: str | None = None, limit: int = 20) -> dict:
    """Search local file names with Spotlight; return paths only, never contents."""
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "query is required", "results": []}
    if not sys.platform == "darwin":
        return {"ok": False, "error": "Spotlight search is available on macOS only", "results": []}
    root = os.path.abspath(os.path.expanduser(directory or "~"))
    if not os.path.isdir(root):
        return {"ok": False, "error": f"directory does not exist: {root}", "results": []}
    limit = max(1, min(int(limit or 20), 100))
    # File-name searches need a plain fragment. Reject control characters and
    # Spotlight query operators rather than letting user text alter the query.
    safe_query = "".join(ch for ch in query if ch.isalnum() or ch in " ._-").strip()
    if not safe_query:
        return {"ok": False, "error": "query must contain filename characters", "results": []}
    escaped = safe_query.replace("'", "\\'")
    predicate = f"kMDItemFSName == '*{escaped}'cd"
    returncode, stdout, stderr, timed_out = _run_streaming(
        ["mdfind", "-onlyin", root, predicate],
        timeout=15, tool="search_files", shell=False)
    if timed_out:
        return {"ok": False, "error": "Spotlight search timed out after 15s",
                "results": []}
    if returncode != 0:
        return {"ok": False, "error": (stderr or "Spotlight search failed").strip(),
                "results": []}
    matches = [line for line in stdout.splitlines() if line.strip()]
    # Spotlight can omit new or unindexed project files. Fall back to a
    # bounded filename-only filesystem walk when its index has no match.
    if not matches:
        returncode, stdout, _stderr, _timed_out = _run_streaming(
            ["find", root, "-type", "d", "(", "-name", ".git", "-o",
             "-name", ".venv", "-o", "-name", "venv", "-o", "-name",
             "node_modules", "-o", "-name", "__pycache__", "-o",
             "-name", ".cache", ")", "-prune", "-o", "-type", "f", "-iname",
             f"*{safe_query}*", "-print"],
            timeout=15, tool="search_files", shell=False)
        matches = [line for line in stdout.splitlines() if line.strip()]
    ignored_dirs = {".git", ".venv", "venv", "node_modules", "__pycache__", ".cache"}
    matches = [
        path for path in matches
        if not (set(os.path.relpath(path, root).split(os.sep)) & ignored_dirs)
    ]
    return {"ok": True, "results": matches[:limit], "count": min(len(matches), limit)}


def make_dirs(path: str) -> dict:
    os.makedirs(path, exist_ok=True)
    return {"ok": True, "path": path}


def open_path(target: str) -> dict:
    """Open a file/URL with the system default handler."""
    if sys.platform == "darwin":
        opener = ["open", target]
    elif os.name == "nt":  # pragma: no cover (macOS-only port)
        opener = ["cmd", "/c", "start", "", target]
    else:  # pragma: no cover (macOS-only port)
        opener = ["xdg-open", target]
    returncode, _stdout, stderr, _timed_out = _run_streaming(
        opener, timeout=15, tool="open_path", shell=False)
    return {"ok": returncode == 0, "returncode": returncode,
            "stderr": (stderr or "").strip()}


def spawn_background(command: str, cwd: str | None = None,
                     log_path: str | None = None,
                     env: dict | None = None) -> dict:
    """Start a detached background process (service bootstrap, watchers)."""
    if log_path:
        os.makedirs(os.path.dirname(os.path.abspath(log_path)) or ".", exist_ok=True)
        out = open(log_path, "ab", buffering=0)
        kwargs = {"stdout": out, "stderr": subprocess.STDOUT}
    else:
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    full_env = dict(os.environ)
    if env:
        full_env.update({str(k): str(v) for k, v in env.items()})
    proc = subprocess.Popen(
        command, shell=True, cwd=cwd, stdin=subprocess.DEVNULL,
        env=full_env, start_new_session=True, **kwargs,
    )
    _emit_line("spawn_background", command, "stdout",
               f"spawned pid {proc.pid}")
    return {"pid": proc.pid}


def kill_process(pid: int, force: bool = False) -> dict:
    try:
        os.kill(int(pid), signal.SIGKILL if force else signal.SIGTERM)
        return {"ok": True}
    except ProcessLookupError:
        return {"ok": False, "error": "no such process"}
    except PermissionError as exc:
        return {"ok": False, "error": str(exc)}


def process_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


TOOLS = {
    "run_command": {"fn": run_command,
                    "description": "Run a shell command, capture output"},
    "read_file": {"fn": read_file, "description": "Read a UTF-8 text file"},
    "write_file": {"fn": write_file, "description": "Write a UTF-8 text file"},
    "write_binary_file": {"fn": write_binary_file,
                          "description": "Write base64-encoded file bytes"},
    "list_dir": {"fn": list_dir, "description": "List directory entries"},
    "file_exists": {"fn": file_exists, "description": "Check path existence"},
    "search_files": {"fn": search_files,
                     "description": "Search local file names through Spotlight; returns paths only"},
    "make_dirs": {"fn": make_dirs, "description": "mkdir -p"},
    "open_path": {"fn": open_path, "description": "Open file/URL with default app"},
    "spawn_background": {"fn": spawn_background,
                         "description": "Start detached background process"},
    "kill_process": {"fn": kill_process, "description": "Terminate a pid"},
    "process_alive": {"fn": process_alive, "description": "Check if a pid is alive"},
}

if __name__ == "__main__":
    serve("shell", TOOLS)
