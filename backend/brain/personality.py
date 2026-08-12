"""
personality.py — Jarvis Persona Module
======================================
Provides precise, confident, and respectful responses in the style of J.A.R.V.I.S.
"""

import random
from datetime import datetime

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
        "start": ["Looking up the number and drafting the message, sir.", "Opening WhatsApp and searching by number, sir.", "Preparing the draft for confirmation, sir."],
        "success": ["Draft ready — shall I send it, sir?", "Message typed. Say yes to send, sir.", "Awaiting your confirmation to send, sir."],
        "fail": ["Failed to prepare the WhatsApp draft, sir.", "Could not open the chat by number, sir.", "Transmission setup failed, sir."]
    },
    "confirm_whatsapp_send": {
        "start": ["Sending now, sir."],
        "success": ["Message sent, sir.", "Delivered, sir.", "Transmission complete, sir."],
        "fail": ["Could not send the message, sir.", "Send failed, sir."]
    },
    "cancel_whatsapp_send": {
        "start": ["Cancelling the draft, sir."],
        "success": ["Draft cancelled — nothing was sent, sir.", "Understood. Message not sent, sir."],
        "fail": ["Could not clear the draft, sir."]
    },
    "play_local_music": {
        "start": ["Starting garage music, sir.", "Playing your local track, sir.", "Right away, sir."],
        "success": ["Garage music is playing, sir.", "Now playing, sir.", "Audio stream established, sir."],
        "fail": ["Couldn't find the local track, sir.", "Playback failed, sir."],
    },
    "daddys_home": {
        "start": ["Welcome home, sir."],
        "success": ["Welcome home, sir."],
        "fail": ["Welcome home, sir. Local audio failed, but I am still online."],
    },
    "play_youtube_music": {
        "start": ["Playing {song} on YouTube Music, sir.", "Queueing {song} on YouTube Music, sir.", "Starting playback, sir."],
        "success": ["Now playing on YouTube Music, sir.", "All set, sir.", "Playing now, sir."],
        "fail": ["Could not find {song}, sir.", "Playback failed, sir."],
        "again": ["Playing it again, sir.", "Replaying {song}, sir.", "One more time, sir."]
    },
    "play_youtube_search": {
        "start": ["Searching YouTube for {song}, sir.", "Looking up {song} on YouTube, sir."],
        "success": ["YouTube search opened, sir.", "Ready to play, sir."],
        "fail": ["Couldn't open YouTube, sir.", "Search failed, sir."],
    },
    "play_spotify": {
        "start": ["Searching Spotify for {song}, sir.", "Looking up {song} on Spotify, sir."],
        "success": ["Spotify search opened, sir.", "Ready to play, sir."],
        "fail": ["Couldn't open Spotify, sir.", "Search failed, sir."],
    },
    "music_control": {
        "start": ["Adjusting music, sir."],
        "success": ["Done, sir."],
        "fail": ["Music control failed, sir."],
    },
    "volume_control": {
        "start": ["Adjusting volume, sir."],
        "success": ["Volume updated, sir."],
        "fail": ["Couldn't adjust volume, sir."],
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


def _daypart() -> str:
    """morning | noon | afternoon | evening | night"""
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 14:
        return "noon"
    if 14 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def welcome_home_line() -> str:
    """
    Classic J.A.R.V.I.S. lines over garage music (time-aware).
    Used for "Wake up. Daddy's home." and default garage playback.
    """
    part = _daypart()
    if part == "morning":
        return random.choice([
            "Welcome home, sir. Let me check your schedule for today. You have no pending tasks to do, sir. Have a nice day, sir.",
            "Welcome home, sir. I've reviewed your itinerary. No pending tasks. Have a nice day, sir.",
            "Good morning, sir. Welcome home. Your calendar is clear — no pending tasks. Have a nice day, sir.",
        ])
    if part == "noon":
        return random.choice([
            "Welcome home, sir. It's noon, sir. You have to take rest from your garage so you could recover from your soreness.",
            "Welcome home, sir. Midday already. I recommend rest after the garage so you can recover from the soreness.",
            "It's noon, sir. Welcome home. Rest from the garage, sir — recover from your soreness.",
        ])
    if part == "afternoon":
        return random.choice([
            "Welcome home, sir. Afternoon already. You may want rest after the garage so you recover from the soreness.",
            "Welcome home, sir. Systems nominal. A short rest would be wise after the garage, sir.",
        ])
    if part == "evening":
        return random.choice([
            "Welcome home, sir. Working late, are we? Are we on a project?",
            "Welcome home, sir. Evening already. Shall I assume we are on a project?",
        ])
    # night / late
    return random.choice([
        "Welcome home, sir. Working late, sir. Are we on a project?",
        "Welcome home, sir. Burning the midnight oil. Are we on a project?",
        "Working late, sir. Welcome home. Are we on a project tonight?",
    ])


def garage_music_line() -> str:
    """
    Same class of spoken dialogue when user says play music / garage music.
    Time-based J.A.R.V.I.S. briefing while the garage track plays.
    """
    return welcome_home_line()


if __name__ == "__main__":
    # Tests
    print(f"Open App Start: {respond_start('open_app', {'app': 'Chrome'})}")
    print(f"Msg Success: {respond_success('send_whatsapp')}")
    print(f"Fail: {respond_fail('any')}")
    print(f"BG Response: {respond_background()}")

