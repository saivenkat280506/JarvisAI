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

    _server_proc = subprocess.Popen(
        ["node", str(SERVER_JS)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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


def command(action: str, timeout: float = 120.0, **params: Any) -> dict:
    """Send a command to the Puppeteer service. Auto-starts server if needed."""
    ensure_server(True)
    # Do not put HTTP timeout into the Puppeteer payload
    params = {k: v for k, v in params.items() if k != "timeout"}
    payload = {"action": action, **params}
    data = json.dumps(payload).encode("utf-8")
    req = Request(
        f"{BASE}/command",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except Exception:
            return {"ok": False, "error": body or str(e)}
    except URLError as e:
        return {"ok": False, "error": f"Puppeteer unreachable: {e}"}


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
