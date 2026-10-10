"""
open_app.py — macOS application launcher
========================================
Resolves names (notepad/calc aliases map to TextEdit/Calculator) and
launches them with `open -a`.
"""

import os
import shutil

from executor.sys_platform import (
    MAC_APP_ALIASES,
    close_application,
    open_application,
    open_url,
    resolve_mac_app,
)


def get_app_path(app_name: str) -> str:
    name_lower = app_name.lower().strip()
    alias = MAC_APP_ALIASES.get(name_lower)
    if alias:
        return alias
    which = shutil.which(name_lower)
    if which:
        return which
    resolved = resolve_mac_app(app_name)
    for base in ("/Applications", os.path.expanduser("~/Applications"), "/System/Applications"):
        candidate = os.path.join(base, f"{resolved}.app")
        if os.path.isdir(candidate):
            return candidate
    return resolved


def close_app(app_name: str):
    return close_application(app_name)


def open_app(app_name: str):
    name_lower = app_name.lower().strip()

    if "whatsapp" in name_lower:
        try:
            from executor.automation import open_whatsapp
            return open_whatsapp()
        except ImportError:
            return open_application("WhatsApp")

    if name_lower in ("browser", "arc"):
        for candidate in ("Arc", "Google Chrome", "Safari"):
            ok, msg = open_application(candidate)
            if ok:
                return True, msg
        try:
            open_url("about:blank")
            return True, "Opened default system browser."
        except Exception as e:
            return False, f"failed to open {app_name}: {str(e)}"

    return open_application(app_name)
