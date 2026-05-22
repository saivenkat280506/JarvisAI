"""
safety.py — Guardrail Layer
============================
Ensures LLM output is valid and safe before execution.
"""

import json

ALLOWED_INTENTS = ["open_app", "send_whatsapp", "play_youtube_music", "search_browser", "cancel_task", "chat"]

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
