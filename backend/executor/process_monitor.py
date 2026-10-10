"""
process_monitor.py — Real-time Application Process Tracker
==========================================================
Monitors active processes in a background thread and logs
when applications are opened or closed.
"""

import threading
import time

from executor.sys_platform import list_process_names

_running_processes = set()
_monitor_thread = None
_monitoring = False

IGNORE_LIST = {
    "kernel_task", "launchd", "cfprefsd", "distnoted", "usereventagent",
    "runningboardd", "windowserver", "loginwindow", "finder", "dock",
    "systemuiserver", "controlcenter", "notificationcenter", "coreaudiod",
    "python", "python3", "node", "npm", "electron", "zsh", "bash",
}


def get_running_processes():
    """Fetches the set of currently running process names."""
    return list_process_names()


def _monitor_loop():
    global _running_processes, _monitoring
    print("[ProcessMonitor] Establishing process baseline...")
    _running_processes = get_running_processes()
    print(f"[ProcessMonitor] Baseline set with {len(_running_processes)} active processes.")

    while _monitoring:
        time.sleep(3.0)
        if not _monitoring:
            break

        try:
            current = get_running_processes()
        except Exception as exc:
            # Shutdown closes the macOS tool server after stopping this thread.
            # A query already in flight can finish during that window.
            if not _monitoring:
                break
            print(f"[ProcessMonitor] Process query failed: {exc}")
            continue

        if not _monitoring:
            break
        if not current:
            continue

        opened = current - _running_processes
        closed = _running_processes - current

        for app in opened:
            if app not in IGNORE_LIST:
                app_clean = app.replace(".app", "").capitalize()
                print(f"[ProcessMonitor] Detected App Opened: {app_clean} ({app})")

        for app in closed:
            if app not in IGNORE_LIST:
                app_clean = app.replace(".app", "").capitalize()
                print(f"[ProcessMonitor] Detected App Closed: {app_clean} ({app})")

        _running_processes = current


def start_process_monitor():
    """Starts the process tracking background service."""
    global _monitor_thread, _monitoring
    if _monitoring:
        return

    _monitoring = True
    _monitor_thread = threading.Thread(target=_monitor_loop, name="JarvisProcessMonitor")
    _monitor_thread.daemon = True
    _monitor_thread.start()
    print("[ProcessMonitor] Background monitoring service launched successfully (macOS).")


def stop_process_monitor():
    """Stops the process tracking background service."""
    global _monitoring
    _monitoring = False
    print("[ProcessMonitor] Background monitoring service stopped.")
