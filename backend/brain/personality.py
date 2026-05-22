"""
personality.py — Jarvis Persona Module
======================================
Provides precise, confident, and respectful responses in the style of J.A.R.V.I.S.
"""

import random

RESPONSE_MAP = {
    "chat": {
        "start": ["{response}"],
        "success": [""],
        "fail": ["I'm sorry, I couldn't respond to that."]
    },
    "open_app": {
        "start": ["Opening {app}, sir.", "Launching {app}, sir.", "Right away, sir. Opening {app}.", "Searching system path for {app} and initiating startup."],
        "success": ["Done, sir.", "Application ready, sir.", "All set, sir.", "Interface deployed, sir."],
        "fail": ["I couldn’t open {app}, sir.", "Unable to locate {app}, sir.", "It appears {app} is not responding or missing from registry."]
    },
    "send_whatsapp": {
        "start": ["Sending message to {name}, sir.", "Messaging {name} now, sir.", "On it, sir.", "Initiating secure transmission to {name}."],
        "success": ["Message sent, sir.", "Done, sir.", "Delivered, sir.", "Transmission successful, sir."],
        "fail": ["Failed to message {name}, sir.", "Transmission failed, sir.", "Connectivity issues are preventing communication with {name}."]
    },
    "play_youtube_music": {
        "start": ["Playing {song}, sir.", "Queueing {song}, sir.", "Starting playback, sir.", "Accessing media nodes for {song}."],
        "success": ["Now playing, sir.", "All set, sir.", "Playing now, sir.", "Audio stream established, sir."],
        "fail": ["Could not find {song}, sir.", "Playback failed, sir.", "No relevant media found for {song} in the archives."],
        "again": ["Playing it again, sir.", "Replaying {song}, sir.", "One more time, sir."]
    },
    "search_browser": {
        "start": ["Searching for {query}, sir.", "Scanning the web, sir.", "Looking it up, sir.", "Querying global knowledge nodes for {query}."],
        "success": ["Information retrieved, sir.", "Found it, sir.", "Results are ready, sir.", "Analysis complete, sir. Results uploaded."],
        "fail": ["No results found, sir.", "Couldn't find that, sir.", "The search yielded no significant data, sir."]
    },
    "general": {
        "start": ["On it, sir.", "Working on it, sir.", "Right away, sir.", "Acknowledged, sir. Processing request."],
        "success": ["Task completed, sir.", "Done, sir.", "All set, sir.", "Operations complete, sir."],
        "fail": ["I couldn’t complete that, sir.", "Task failed, sir.", "Internal systems were unable to fulfill the request."]
    },
    "news": {
        "start": ["Checking the latest headlines, sir.", "Pulling the news feed, sir.", "Scanning current events."],
        "success": ["Here's what's happening right now, sir.", "Latest briefing ready, sir."],
        "fail": ["Couldn't retrieve headlines at this moment, sir."]
    },
    "joke": {
        "start": ["One moment, sir.", "Let me think of something appropriate."],
        "success": [""],
        "fail": ["My humor subroutines appear to be offline, sir."]
    },
    "intro": {
        "start": ["Of course, sir."],
        "success": [""],
        "fail": ["I seem to be having trouble with self-reflection, sir."]
    },
    "focus_window": {
        "start": ["Returning to the interface, sir."],
        "success": ["Back in focus, sir."],
        "fail": ["Couldn't restore focus, sir."]
    },
    "background": {
        "start": [
            "I’ll handle that, sir.",
            "Running in background, sir.",
            "Processing that in the background, sir.",
            "I'll keep an eye on that, sir."
        ]
    },
    "cancel": {
        "success": [
            "Stopped, sir.",
            "Cancelled, sir.",
            "Task terminated, sir."
        ],
        "fail": [
            "Nothing running to stop, sir.",
            "No active tasks to cancel, sir."
        ]
    }
}

def _get_template(intent: str, phase: str) -> str:
    """Retrieves a random template for a given intent and phase."""
    category = RESPONSE_MAP.get(intent, RESPONSE_MAP["general"])
    return random.choice(category.get(phase, RESPONSE_MAP["general"][phase]))

def respond_start(intent: str, params: dict = None) -> str:
    """Immediate feedback before action starts."""
    template = _get_template(intent, "start")
    params = params or {}
    try:
        return template.format(**params)
    except KeyError:
        return template

def respond_success(intent: str, params: dict = None) -> str:
    """Confirmation after successful action."""
    # Check for "again" context in params
    if params and params.get("is_again"):
        template = random.choice(RESPONSE_MAP.get(intent, RESPONSE_MAP["general"]).get("again", ["Done, sir."]))
        try:
            return template.format(**params)
        except KeyError:
            return template
    
    template = _get_template(intent, "success")
    params = params or {}
    try:
        return template.format(**params)
    except KeyError:
        return template

def respond_fail(intent: str, params: dict = None) -> str:
    """Respectful failure notification."""
    return "I couldn’t complete that, sir."

def respond_background(intent: str = None, params: dict = None) -> str:
    """Notification for non-blocking background tasks."""
    return random.choice(RESPONSE_MAP["background"]["start"])

def respond_cancel(success: bool = True) -> str:
    """Response for task cancellation."""
    if success:
        return random.choice(RESPONSE_MAP["cancel"]["success"])
    else:
        return random.choice(RESPONSE_MAP["cancel"]["fail"])

def respond_processing() -> str:
    """Immediate response when processing takes time (LLM route)."""
    return random.choice([
        "Working on it, sir.",
        "Just a moment, sir.",
        "Processing that, sir.",
        "One moment, sir."
    ])

if __name__ == "__main__":
    # Tests
    print(f"Open App Start: {respond_start('open_app', {'app': 'Chrome'})}")
    print(f"Msg Success: {respond_success('send_whatsapp')}")
    print(f"Fail: {respond_fail('any')}")
    print(f"BG Response: {respond_background()}")

