"""
tools_registry.py — Tool Mapping
===============================
Maps intent strings to actual Python functions.
"""

import os

from executor.open_app import open_app
from mcp_clients import shell as mcp_shell
from executor.automation import (
    send_whatsapp_message,
    confirm_send_whatsapp_message,
    cancel_whatsapp_draft,
    search_google,
    read_news_headlines,
    smart_search,
)
from brain.memory_store import (
    remember_fact,
    parse_remember_text,
    recall,
    add_task,
    list_tasks,
    complete_task,
    list_remembered_contacts,
)
from brain.memory import save_memory, get_memory
from executor.music_services import (
    play_local_garage,
    stop_local_music,
    music_control,
)
from executor.volume_control import volume_control
from executor.task_manager import task_manager
from executor.browser_puppeteer import (
    play_youtube_music_pp,
    play_youtube_search_pp,
    play_spotify_pp,
    browser_action,
    spotify_login_puppeteer,
    scroll_test_puppeteer,
    browser_click,
    browser_type,
    browser_scroll,
    browser_navigate,
    linkedin_browser_demo,
    web_search_puppeteer,
)

# Registry mapping LLM intents to functions
from executor.automation import search_and_summarize_in_notepad


def _calculate_tool(params: dict):
    """Perform parsed arithmetic without eval()."""
    left = float(params.get("left"))
    right = float(params.get("right"))
    operator = str(params.get("operator", "plus")).lower()
    if operator in ("plus", "+"):
        value = left + right
    elif operator in ("minus", "-"):
        value = left - right
    elif operator in ("times", "*", "x", "multiplied by"):
        value = left * right
    elif operator in ("divided by", "/", "over"):
        if right == 0:
            return False, "I cannot divide by zero, sir."
        value = left / right
    else:
        return False, "I could not identify that arithmetic operation, sir."
    formatted = str(int(value)) if value.is_integer() else f"{value:.6g}"
    return True, f"The result is {formatted}, sir."


def _usable_whatsapp_number(number: str) -> str:
    """Drop LLM placeholders such as '+91...' before they reach automation."""
    raw = (number or "").strip()
    if not raw or "..." in raw or "…" in raw:
        return ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    return raw if len(digits) >= 10 else ""


def _send_whatsapp_tool(params: dict):
    """
    Open WhatsApp → Sathish chat only → type a draft. Send is frozen.
    """
    name = (
        params.get("name")
        or params.get("contact")
        or params.get("to")
        or ""
    )
    number = _usable_whatsapp_number(params.get("number") or params.get("phone") or "")
    message = params.get("message") or params.get("text") or ""
    request = {"name": name, "number": number, "message": message}
    success, result = send_whatsapp_message(name, message=message, number=number)
    if success:
        save_memory("last_contact", name or number)
    return success, result


def _send_whatsapp_all_tool(params: dict):
    message = (params or {}).get("message") or (params or {}).get("text") or ""
    error = (params or {}).get("error") or ""
    if error:
        return False, error
    if not message.strip():
        return False, (
            "Say the message and 'all contacts' together, for example: "
            "send hello from Jarvis to all contacts."
        )
    request = {"all_contacts": True, "message": message}
    save_memory("last_whatsapp_request", request)
    save_memory("pending_whatsapp", {**request, "awaiting_confirm": True})
    return True, (
        "I have a WhatsApp message ready for all saved contacts. "
        "Say ‘yes’ to confirm sending it, or ‘cancel’ to discard it."
    )


def _remember_tool(params: dict):
    text = (params or {}).get("text") or ""
    key = (params or {}).get("key") or ""
    value = (params or {}).get("value") or ""
    if not key or not value:
        key, value = parse_remember_text(text)
    return remember_fact(key, value)


def _recall_tool(params: dict):
    query = (params or {}).get("query") or (params or {}).get("text") or ""
    if query.strip().lower() in {"contacts", "contact", "whatsapp list"}:
        return list_remembered_contacts()
    return recall(query)


def _add_task_tool(params: dict):
    return add_task((params or {}).get("title") or (params or {}).get("task") or "")


def _list_tasks_tool(params: dict = None):
    return list_tasks(((params or {}).get("status") or "open"))


def _complete_task_tool(params: dict):
    return complete_task((params or {}).get("query") or (params or {}).get("title") or "")


def _search_files_tool(params: dict):
    query = ((params or {}).get("query") or "").strip()
    if not query:
        return False, "Tell me the file name to search for, sir."
    result = mcp_shell.search_files(query, directory=(params or {}).get("directory"))
    if not result.get("ok"):
        return False, result.get("error", "Local file search failed.")
    paths = result.get("results", [])
    if not paths:
        return True, f"I couldn't find a local file matching {query!r}."
    return True, "Found local files:\n" + "\n".join(paths)


_HOME_DIRS = {
    "desktop": "Desktop",
    "documents": "Documents",
    "downloads": "Downloads",
    "pictures": "Pictures",
    "movies": "Movies",
    "music": "Music",
}

_MAX_SHELL_LINES = 40


def _run_command_tool(params: dict):
    """Run one shell command through the shell MCP server.

    Going through mcp_shell (never subprocess) is what streams every stdout /
    stderr line to the terminal block as `terminal_output` events and emits
    `terminal_exit` with the return code.
    """
    params = params or {}
    command = str(params.get("command") or params.get("cmd") or "").strip()
    if not command:
        return False, "Which command should I run, sir?"
    cwd = params.get("cwd") or None
    if isinstance(cwd, str):
        cwd = cwd.strip() or None
        if cwd and (cwd.startswith("~") or not os.path.isabs(cwd)):
            cwd = os.path.expanduser(cwd)
    try:
        timeout = float(params.get("timeout") or 30.0)
    except (TypeError, ValueError):
        timeout = 30.0
    result = mcp_shell.run_command(command, cwd=cwd, timeout=timeout)
    code = result.get("returncode")
    stdout = (result.get("stdout") or "").strip()
    stderr = (result.get("stderr") or "").strip()
    if code == 0:
        text = stdout or "(no output)"
    else:
        text = stderr or stdout or "The command produced no output."
    lines = text.splitlines()
    if len(lines) > _MAX_SHELL_LINES:
        text = "\n".join(lines[:_MAX_SHELL_LINES]) + (
            f"\n… {len(lines) - _MAX_SHELL_LINES} more line(s)"
        )
    if result.get("timed_out"):
        return False, f"{text}\n(command timed out after {timeout:g}s)"
    if code not in (0, None):
        return False, f"{text}\n(exit {code})"
    return True, text


def _resolve_target_dir(directory) -> str:
    """Map a model-provided location onto a real directory under $HOME."""
    raw = str(directory or "").strip()
    home = os.path.expanduser("~")
    if not raw:
        return os.path.join(home, "Desktop")
    if raw.startswith("~") or os.path.isabs(raw):
        return os.path.expanduser(raw)
    known = _HOME_DIRS.get(raw.lower())
    if known:
        return os.path.join(home, known)
    if raw.lower() in ("home", "home folder"):
        return home
    return os.path.join(home, raw)


def _target_path(name: str, directory) -> str:
    name = str(name or "").strip()
    if os.path.isabs(name):
        return name
    return os.path.join(_resolve_target_dir(directory), name)


def _create_file_tool(params: dict):
    params = params or {}
    name = str(
        params.get("file_name") or params.get("filename") or params.get("name") or ""
    ).strip()
    if not name:
        return False, "What should I name the file, sir?"
    path = _target_path(name, params.get("path") or params.get("directory"))
    content = params.get("content")
    content = "" if content is None else str(content)
    result = mcp_shell.write_file(path, content)
    written = result.get("bytes", len(content.encode("utf-8")))
    return True, f"Created {path} ({written} bytes), sir."


def _create_folder_tool(params: dict):
    params = params or {}
    name = str(
        params.get("folder_name") or params.get("directory_name")
        or params.get("name") or ""
    ).strip()
    if not name:
        return False, "What should I name the folder, sir?"
    path = _target_path(name, params.get("path") or params.get("directory"))
    mcp_shell.make_dirs(path)
    return True, f"Created folder {path}, sir."


def _confirm_whatsapp_tool(params: dict = None):
    pending = get_memory("pending_whatsapp") or {}
    if not isinstance(pending, dict) or not pending.get("awaiting_confirm") or not pending.get("message"):
        return False, "There is no WhatsApp message waiting for confirmation, sir."
    success, result = confirm_send_whatsapp_message()
    if success:
        save_memory("pending_whatsapp", None)
        save_memory("last_whatsapp_request", None)
    return success, result


def _cancel_whatsapp_tool(params: dict = None):
    success, result = cancel_whatsapp_draft()
    return success, result


TOOL_MAP = {
    "open_app": lambda params: open_app(params.get("app", "textedit")),
    "search_files": lambda params: _search_files_tool(params),
    # Terminal path — every call streams into the in-app terminal block.
    "run_command": lambda params: _run_command_tool(params),
    "create_file": lambda params: _create_file_tool(params),
    "create_folder": lambda params: _create_folder_tool(params),
    "send_whatsapp": lambda params: _send_whatsapp_tool(params),
    "send_whatsapp_all": lambda params: _send_whatsapp_all_tool(params),
    "confirm_whatsapp_send": lambda params: _confirm_whatsapp_tool(params),
    "cancel_whatsapp_send": lambda params: _cancel_whatsapp_tool(params),
    "remember": lambda params: _remember_tool(params),
    "recall": lambda params: _recall_tool(params),
    "add_task": lambda params: _add_task_tool(params),
    "list_tasks": lambda params: _list_tasks_tool(params),
    "complete_task": lambda params: _complete_task_tool(params),
    # Garage track only — volume is local mixer % (not system volume)
    "play_local_music": lambda params: play_local_garage(
        int((params or {}).get("volume") or 50)
    ),
    "daddys_home": lambda params: play_local_garage(
        int((params or {}).get("volume") or 50)
    ),
    # Puppeteer-powered advanced browser music automation
    "play_youtube_music": play_youtube_music_pp,
    "play_youtube_search": play_youtube_search_pp,
    "play_spotify": play_spotify_pp,
    "spotify_login": spotify_login_puppeteer,
    "browser_action": browser_action,
    "browser_scroll_test": scroll_test_puppeteer,
    "browser_click": browser_click,
    "browser_type": browser_type,
    "browser_scroll": browser_scroll,
    "browser_navigate": browser_navigate,
    "linkedin_browser_demo": linkedin_browser_demo,
    # Search opens Chrome beside Jarvis and returns result snippets
    "search_browser": web_search_puppeteer,
    "web_search": web_search_puppeteer,
    "music_control": lambda params: music_control(params),
    "volume_control": lambda params: volume_control(params),
    "calculate": lambda params: _calculate_tool(params),
    # Keep text-only summarize tools under smart_search / read_headlines
    "search_and_summarize": lambda params: search_and_summarize_in_notepad(params.get("query", "latest news")),
    "read_headlines": lambda params: read_news_headlines(params.get("query", "")),
    "smart_search": lambda params: smart_search(params.get("query", "")),
    "chat": lambda params: (True, "Conversation handled."),
}

def cancel_task(params: dict):
    """Cancels tasks by type or all."""
    task_type = params.get("task_type", "all")
    stopped_music = False

    if task_type in ("music", "all"):
        stopped_music = stop_local_music()

    if task_type == "all":
        count = 0
        for tid in list(task_manager.active_tasks.keys()):
            if task_manager.cancel_task(tid):
                count += 1
        if count > 0 or stopped_music:
            return True, f"Stopped {count} task(s)" if count else "Music stopped, sir."
        return False, "No active tasks"
    else:
        count = task_manager.cancel_task_by_type(task_type)
        if count > 0 or stopped_music:
            return True, f"Stopped {count} task(s) of type {task_type}" if count else "Music stopped, sir."
        return False, f"No active {task_type} tasks"

TOOL_MAP["cancel_task"] = cancel_task

def get_tool(intent: str):
    """Returns the function associated with the intent."""
    return TOOL_MAP.get(intent)
