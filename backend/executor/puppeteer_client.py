"""
puppeteer_client.py — Python client for the JARVIS Puppeteer control plane.
==========================================================================
Starts backend/browser-automation/src/server.mjs on demand and sends JSON commands.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mcp_clients import shell as mcp_shell

HOST = "127.0.0.1"
PORT = int(os.environ.get("PUPPETEER_PORT", "3920"))
BASE = f"http://{HOST}:{PORT}"

# JARVIS/backend/browser-automation
ROOT = Path(__file__).resolve().parents[1] / "browser-automation"
SERVER_JS = ROOT / "src" / "server.mjs"

# Node control-plane is spawned via the shell MCP server (spawn_background);
# we keep only its pid here. Documented service-bootstrap exception: the
# process must outlive the request that started it, so it runs detached.
_server_pid: Optional[int] = None
_active_chrome_profile: str | None = None


def _health(timeout: float = 1.5) -> bool:
    try:
        with urlopen(f"{BASE}/health", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return bool(data.get("ok"))
    except Exception:
        return False


def _ensure_chrome_profile_clone(profile_dir: str) -> Path:
    """Keep a private JARVIS copy of the selected signed-in Chrome profile."""
    clone_root = ROOT / "chrome-profile-data"
    clone_profile = clone_root / profile_dir
    if (clone_profile / "Preferences").is_file():
        return clone_root

    from executor.sys_platform import chrome_user_data

    result = mcp_shell.run_command(
        "node src/sync_chrome_profile.mjs",
        cwd=str(ROOT),
        timeout=300.0,
        env={
            "CHROME_USER_DATA": chrome_user_data(),
            "CHROME_PROFILE_DIRECTORY": profile_dir,
            "JARVIS_CHROME_PROFILE_DIRECTORY": profile_dir,
        },
    )
    if result.get("returncode") != 0 or not (clone_profile / "Preferences").is_file():
        detail = (result.get("stderr") or result.get("stdout") or "").strip()[:400]
        raise RuntimeError(
            f"Could not prepare the JARVIS copy of Chrome profile {profile_dir}. "
            "Close that Chrome profile and try again. " + detail
        )
    return clone_root


def ensure_server(start_if_needed: bool = True) -> bool:
    """Return True if the Puppeteer control plane is reachable."""
    global _server_pid, _active_chrome_profile
    from brain.settings import get_settings
    from executor.sys_platform import chrome_profile_directory_for_email, chrome_user_data

    profile_email = str(
        get_settings().get("chromeProfileEmail") or os.environ.get("CHROME_GOOGLE_EMAIL", "")
    ).strip().lower()
    profile_dir = chrome_profile_directory_for_email(profile_email)
    if not profile_dir:
        raise RuntimeError(
            f"Chrome profile for {profile_email} was not found. Sign into that profile in Chrome, then try again."
        )
    profile_key = f"{profile_email}:{profile_dir}"
    clone_root = _ensure_chrome_profile_clone(profile_dir)

    if _health():
        if _active_chrome_profile == profile_key:
            return True
        try:
            payload = json.dumps({
                "action": "configure_profile",
                "params": {
                    "userDataDir": str(clone_root),
                    "profileDir": profile_dir,
                    "useRealProfile": False,
                },
            }).encode("utf-8")
            req = Request(
                f"{BASE}/command",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=10.0) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            if not result.get("ok"):
                raise RuntimeError(result.get("error") or "Chrome profile could not be selected.")
            _active_chrome_profile = profile_key
            return True
        except Exception as exc:
            print(f"[Puppeteer] Restarting control plane to apply Chrome profile: {exc}")
            _kill_server()
            _active_chrome_profile = None
    if not start_if_needed:
        return False
    if not SERVER_JS.exists():
        raise FileNotFoundError(f"Puppeteer server not found: {SERVER_JS}")

    # Install deps once if needed. Skip Puppeteer's Chrome-for-Testing download;
    # macOS automation uses the installed Google Chrome binary.
    node_modules = ROOT / "node_modules" / "puppeteer" / "package.json"
    if not node_modules.exists():
        print("[Puppeteer] Installing npm dependencies (first run)...")
        result = mcp_shell.run_command(
            "npm install",
            cwd=str(ROOT),
            timeout=600.0,
            env={"PUPPETEER_SKIP_DOWNLOAD": "true"},
        )
        if result.get("returncode") != 0:
            raise RuntimeError(
                f"npm install failed: {(result.get('stderr') or result.get('stdout') or '').strip()[:400]}"
            )

    env = os.environ.copy()
    env.setdefault("PUPPETEER_HOST", HOST)
    env.setdefault("PUPPETEER_PORT", str(PORT))
    env["PUPPETEER_SKIP_DOWNLOAD"] = "true"
    from executor.sys_platform import chrome_executable
    chrome_bin = chrome_executable()
    if chrome_bin:
        env.setdefault("CHROME_PATH", chrome_bin)
        env.setdefault("PUPPETEER_EXECUTABLE_PATH", chrome_bin)
    # Resolve the selected account to its Chrome profile directory before launching.
    env["CHROME_USE_REAL_PROFILE"] = "0"
    env["CHROME_USER_DATA"] = str(clone_root)
    env["CHROME_PROFILE_EMAIL"] = profile_email
    env["CHROME_PROFILE_DIRECTORY"] = profile_dir
    env["CHROME_FALLBACK_ON_LOCK"] = env.get("CHROME_FALLBACK_ON_LOCK") or "1"

    log_path = ROOT / "puppeteer-server.log"
    log_f = open(log_path, "a", encoding="utf-8")
    log_f.write(f"\n--- start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    log_f.write("Control plane requested by the browser MCP server.\n")
    log_f.flush()
    log_f.close()

    spawned = mcp_shell.spawn_background(
        f"node {shlex.quote(str(SERVER_JS))}",
        cwd=str(ROOT),
        log_path=str(log_path),
        env=env,
    )
    _server_pid = spawned.get("pid")

    # Wait for health
    deadline = time.time() + 45
    while time.time() < deadline:
        if _health(timeout=2.0):
            _active_chrome_profile = profile_key
            print(f"[Puppeteer] Control plane ready at {BASE}")
            return True
        if not _server_pid or not mcp_shell.process_alive(_server_pid):
            break
        time.sleep(0.4)

    raise RuntimeError(
        f"Puppeteer server failed to start on {BASE}. "
        f"Try: cd backend/browser-automation && npm install && npm start"
    )


def _kill_server() -> None:
    global _server_pid
    if _server_pid and mcp_shell.process_alive(_server_pid):
        try:
            mcp_shell.kill_process(_server_pid, force=False)
            for _ in range(15):
                if not mcp_shell.process_alive(_server_pid):
                    break
                time.sleep(0.2)
            if mcp_shell.process_alive(_server_pid):
                mcp_shell.kill_process(_server_pid, force=True)
        except Exception:
            pass
    _server_pid = None
    # Free only JARVIS-launched Chrome (clone profile / this server). Never kill daily Chrome.
    try:
        from executor.sys_platform import kill_matching_command
        kill_matching_command(str(ROOT / "chrome-profile-data"))
        kill_matching_command(str(ROOT / "user-data"))
        kill_matching_command(str(SERVER_JS))
    except Exception:
        pass


def command(action: str, timeout: float = 120.0, **params: Any) -> dict:
    """Send a command to the Puppeteer service. Auto-starts server if needed.

    Retries once after restarting the control plane if the connection drops
    mid-command (common after long YouTube Music runs).
    """
    # Do not put HTTP timeout into the Puppeteer payload
    params = {k: v for k, v in params.items() if k != "timeout"}
    payload = {"action": action, **params}
    data = json.dumps(payload).encode("utf-8")

    last_err: Exception | None = None
    for attempt in range(2):
        try:
            ensure_server(True)
            req = Request(
                f"{BASE}/command",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                return json.loads(body)
            except Exception:
                return {"ok": False, "error": body or str(e)}
        except (URLError, TimeoutError, ConnectionError, OSError) as e:
            last_err = e
            print(f"[Puppeteer] command '{action}' failed (attempt {attempt+1}): {e}")
            _kill_server()
            time.sleep(1.5)
            continue

    return {"ok": False, "error": f"Puppeteer unreachable after retry: {last_err}"}


def tool_result(result: dict, default_ok_message: str = "Done.") -> tuple[bool, str]:
    """Convert service JSON into (success, spoken message)."""
    if not result:
        return False, "Browser automation returned no result."
    if result.get("ok") is False:
        return False, result.get("message") or result.get("error") or "Browser automation failed."
    msg = result.get("message") or default_ok_message
    # Append useful metrics for scroll tests
    if "avgScrollMs" in result:
        msg += f" Average scroll time {result['avgScrollMs']} ms."
    return True, msg
