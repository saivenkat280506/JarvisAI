"""
safety.py — Guardrail Layer
============================
Ensures LLM output is valid and safe before execution.
"""

import json

ALLOWED_INTENTS = [
    "open_app", "send_whatsapp", "confirm_whatsapp_send", "cancel_whatsapp_send",
    "play_local_music", "daddys_home", "play_youtube_music",
    "play_youtube_search", "play_spotify", "music_control", "volume_control", "search_browser",
    "cancel_task", "chat", "greeting", "capabilities", "news", "joke",
    "qa", "intro", "focus_window", "web_agent", "smart_search", "read_headlines",
    "time",
    # Puppeteer browser automation (wired end-to-end)
    "spotify_login", "browser_action", "browser_scroll_test",
    "browser_click", "browser_type", "browser_scroll", "browser_navigate",
    "linkedin_browser_demo", "web_search", "search_and_summarize",
]

def validate_action(action_json: dict):
    """
    Checks if the LLM response is a valid tool call.
    Returns: (is_safe, validated_json)
    """
    if not isinstance(action_json, dict):
        return False, {"intent": "search_browser", "parameters": {}}
    
    intent = action_json.get("intent")
    
    if intent not in ALLOWED_INTENTS:
        # Fallback to search if intent is invalid
        return False, {
            "intent": "search_browser", 
            "parameters": {"query": "Default search fallback"}
        }
    
    # Basic parameter check
    if "parameters" not in action_json or not isinstance(action_json["parameters"], dict):
        return False, {
            "intent": "search_browser", 
            "parameters": {}
        }
        
    return True, action_json
