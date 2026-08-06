"""
tools_registry.py — Tool Mapping
===============================
Maps intent strings to actual Python functions.
"""

from executor.open_app import open_app
from executor.automation import send_whatsapp_message, search_google, read_news_headlines, smart_search
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
)

# Registry mapping LLM intents to functions
from executor.automation import search_and_summarize_in_notepad

TOOL_MAP = {
    "open_app": lambda params: open_app(params.get("app", "notepad")),
    "send_whatsapp": lambda params: send_whatsapp_message(params.get("name", ""), params.get("message", "")),
    "play_local_music": lambda params: play_local_garage(),
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
    "music_control": lambda params: music_control(params),
    "volume_control": lambda params: volume_control(params),
    "search_browser": lambda params: search_and_summarize_in_notepad(params.get("query", "latest news")),
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
