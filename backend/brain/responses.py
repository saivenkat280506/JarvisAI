"""
responses.py — Concise Voice Responses
=====================================
"""

RESPONSES = {
    # Bridging responses (immediate)
    "open_app_start": "Initiating application startup, sir.",
    "send_whatsapp_start": "Preparing transmission.",
    "play_music_start": "Accessing the media archives.",
    "search_start": "Scanning global knowledge nodes.",
    "general_start": "Processing your request, sir.",
    
    # Final responses (completion)
    "open_app_success": "Interface deployed.",
    "open_app_fail": "I encountered an error while launching the application.",
    "send_whatsapp_success": "Transmission complete, sir.",
    "send_whatsapp_fail": "Communication link failed.",
    "play_music_success": "Audio stream established.",
    "play_music_fail": "The requested media could not be located.",
    "search_success": "The results are ready for your review.",
    "search_fail": "The analysis yielded no results.",
    "unknown": "I'm afraid I don't have the protocol for that, sir.",
    "standby": "At your service, sir.",
}

def get_response(key: str):
    """Returns a short, natural response."""
    return RESPONSES.get(key, "Processed.")

