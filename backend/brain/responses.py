"""
responses.py — Concise Voice Responses
=====================================
"""

RESPONSES = {
    "open_app_start": "Will do, sir.",
    "send_whatsapp_start": "Working on it, sir.",
    "play_music_start": "As you wish.",
    "search_start": "Looking that up, sir.",
    "general_start": "On it, sir.",
    "open_app_success": "Done, sir.",
    "open_app_fail": "I couldn't open that, sir.",
    "send_whatsapp_success": "Sent, sir.",
    "send_whatsapp_fail": "That didn't go through, sir.",
    "play_music_success": "Playing now, sir.",
    "play_music_fail": "I couldn't find that track, sir.",
    "search_success": "Query complete, sir.",
    "search_fail": "Nothing useful came back, sir.",
    "unknown": "I don't have a protocol for that, sir.",
    "standby": "At your service, sir.",
}

def get_response(key: str):
    """Returns a short, natural response."""
    return RESPONSES.get(key, "Processed.")

