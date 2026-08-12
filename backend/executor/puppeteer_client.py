"""
puppeteer_client.py — Python client for the JARVIS Puppeteer control plane.
==========================================================================
Starts browser-automation/src/server.mjs on demand and sends JSON commands.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HOST = os.environ.get("PUPPETEER_HOST", "127.0.0.1")
PORT = int(os.environ.get("PUPPETEER_PORT", "3920"))
BASE = f"http://{HOST}:{PORT}"

# JARVIS/browser-automation
ROOT = Path(__file__).resolve().parents[2] / "browser-automation"
SERVER_JS = ROOT / "src" / "server.mjs"

_server_proc: Optional[subprocess.Popen] = None


def _health(timeout: float = 1.5) -> bool:
    try:
        with urlopen(f"{BASE}/health", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return bool(data.get("ok"))
    except Exception:
        return False


def ensure_server(start_if_needed: bool = True) -> bool:
    """Return True if the Puppeteer control plane is reachable."""
    global _server_proc
    if _health():
        return True
    if not start_if_needed:
        return False
    if not SERVER_JS.exists():
        raise FileNotFoundError(f"Puppeteer server not found: {SERVER_JS}")

    # Install deps once if needed
    node_modules = ROOT / "node_modules" / "puppeteer"
    if not node_modules.exists():
        print("[Puppeteer] Installing npm dependencies (first run)...")
        subprocess.run(
            ["npm", "install"],
            cwd=str(ROOT),
            check=True,
            shell=sys.platform == "win32",
        )

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    env = os.environ.copy()
    env.setdefault("PUPPETEER_HOST", HOST)
    env.setdefault("PUPPETEER_PORT", str(PORT))
    clone = ROOT / "chrome-profile-data"

    # Live Chrome User Data cannot be shared with an open Chrome window
    # (SingletonLock). Default ALWAYS to the dedicated JARVIS clone.
    # Opt into the live profile only with BOTH:
    #   CHROME_USE_REAL_PROFILE=1  and  CHROME_ALLOW_LIVE_PROFILE=1
    # (and close daily Chrome first).
    allow_live = str(env.get("CHROME_ALLOW_LIVE_PROFILE", "0")).strip().lower() in (
        "1",
        "true",
        "yes",
    )
    want_real = str(env.get("CHROME_USE_REAL_PROFILE", "0")).strip().lower() in (
        "1",
        "true",
        "yes",
    )
    use_real = allow_live and want_real

    if not use_real:
        env["CHROME_USE_REAL_PROFILE"] = "0"
        env["CHROME_USER_DATA"] = str(clone)
        env["CHROME_KILL_BEFORE_LAUNCH"] = env.get("CHROME_KILL_BEFORE_LAUNCH") or "1"
    else:
        env["CHROME_USE_REAL_PROFILE"] = "1"
        env.setdefault(
            "CHROME_USER_DATA",
            str(Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "User Data"),
        )
        env["CHROME_KILL_BEFORE_LAUNCH"] = "1"

    env["CHROME_FALLBACK_ON_LOCK"] = env.get("CHROME_FALLBACK_ON_LOCK") or "1"
    env.setdefault("CHROME_PROFILE_DIRECTORY", "Default")
    env.setdefault("CHROME_GOOGLE_EMAIL", "challasaivenkat06@gmail.com")
    env.setdefault("SPOTIFY_GOOGLE_EMAIL", "challasaivenkat06@gmail.com")

    log_path = ROOT / "puppeteer-server.log"
    log_f = open(log_path, "a", encoding="utf-8")
    log_f.write(f"\n--- start {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    log_f.write(f"CHROME_USE_REAL_PROFILE={env.get('CHROME_USE_REAL_PROFILE')}\n")
    log_f.write(f"CHROME_PROFILE_DIRECTORY={env.get('CHROME_PROFILE_DIRECTORY')}\n")
    log_f.write(f"CHROME_USER_DATA={env.get('CHROME_USER_DATA')}\n")
    log_f.write(f"CHROME_KILL_BEFORE_LAUNCH={env.get('CHROME_KILL_BEFORE_LAUNCH')}\n")
    log_f.flush()

    _server_proc = subprocess.Popen(
        ["node", str(SERVER_JS)],
        cwd=str(ROOT),
        env=env,
        stdout=log_f,
        stderr=log_f,
        creationflags=creationflags,
    )

    # Wait for health
    deadline = time.time() + 45
    while time.time() < deadline:
        if _health(timeout=2.0):
            print(f"[Puppeteer] Control plane ready at {BASE}")
            return True
        if _server_proc.poll() is not None:
            break
        time.sleep(0.4)

    raise RuntimeError(
        f"Puppeteer server failed to start on {BASE}. "
        f"Try: cd browser-automation && npm install && npm start"
    )


def _kill_server() -> None:
    global _server_proc
    if _server_proc and _server_proc.poll() is None:
        try:
            _server_proc.terminate()
            _server_proc.wait(timeout=3)
        except Exception:
            try:
                _server_proc.kill()
            except Exception:
                pass
    _server_proc = None
    # Free port + chrome locks left by crashed control plane
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "chrome.exe", "/T"],
                capture_output=True,
                check=False,
            )
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
