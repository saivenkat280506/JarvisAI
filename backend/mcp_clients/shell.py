"""
shell.py — Typed wrappers for the `shell` MCP slot.

Backend code must use these helpers instead of subprocess.run/Popen for
command execution, file reads/writes, directory listings, and process
control. (Service bootstrap that MUST host its own child — e.g. the
puppeteer node service — is the documented exception and lives in
executor/puppeteer_client.py.)
"""

from __future__ import annotations

from typing import Any

from . import registry


def run_command(command: str, cwd: str | None = None, timeout: float = 30.0,
                env: dict | None = None) -> dict[str, Any]:
    """Run a shell command. Returns {"returncode","stdout","stderr", "timed_out"?}.
    Never raises for command failures; raises MCPError only if the server dies."""
    return registry.call(
        "shell", "run_command",
        {"command": command, "cwd": cwd, "timeout": timeout, "env": env},
        timeout=timeout + 20.0,
    )


def read_file(path: str, max_bytes: int = 2_000_000) -> str:
    return registry.call("shell", "read_file", {"path": path, "max_bytes": max_bytes})


def write_file(path: str, content: str, append: bool = False) -> dict[str, Any]:
    return registry.call("shell", "write_file",
                         {"path": path, "content": content, "append": append})


def write_binary_file(path: str, content_base64: str,
                      append: bool = False) -> dict[str, Any]:
    """Write base64-encoded bytes via shell MCP (for locally captured images)."""
    return registry.call(
        "shell", "write_binary_file",
        {"path": path, "content_base64": content_base64, "append": append},
    )


def list_dir(path: str) -> list[str]:
    return registry.call("shell", "list_dir", {"path": path})


def file_exists(path: str) -> bool:
    return registry.call("shell", "file_exists", {"path": path})


def search_files(query: str, directory: str | None = None,
                 limit: int = 20) -> dict[str, Any]:
    """Find local file paths with Spotlight through the shell MCP server."""
    return registry.call(
        "shell", "search_files",
        {"query": query, "directory": directory, "limit": int(limit)},
        timeout=20.0,
    )


def make_dirs(path: str) -> dict[str, Any]:
    return registry.call("shell", "make_dirs", {"path": path})


def open_path(target: str) -> dict[str, Any]:
    """Open a file/URL with the system default handler (macOS `open`)."""
    return registry.call("shell", "open_path", {"target": target})


def spawn_background(command: str, cwd: str | None = None,
                     log_path: str | None = None,
                     env: dict | None = None) -> dict[str, Any]:
    """Start a detached background process. Returns {"pid": int}."""
    return registry.call("shell", "spawn_background",
                         {"command": command, "cwd": cwd, "log_path": log_path,
                          "env": env})


def kill_process(pid: int, force: bool = False) -> dict[str, Any]:
    return registry.call("shell", "kill_process", {"pid": int(pid), "force": force})


def process_alive(pid: int) -> bool:
    return registry.call("shell", "process_alive", {"pid": int(pid)})
