"""
router.py — Fast Rule-based Intent Router
==========================================
Routes explicit commands directly to tool intents to avoid LLM latency.

Intent catalogue:
  chat, send_whatsapp, play_local_music, play_youtube_music,
  play_youtube_search, play_spotify, open_app,
  search_browser, cancel_task,
  news, joke, intro, focus_window
"""

import re

_WAKE_PREFIXES = (
    "hey jarvis", "hi jarvis", "hello jarvis",
    "wake up jarvis", "wake jarvis", "jarvis",
)
_GREETING_RE = re.compile(
    r"^(?:hi|hello|hey|good\s+(?:morning|afternoon|evening))"
    r"(?:\s+jarvis)?$",
    re.IGNORECASE,
)


def _strip_wake_prefix(text: str) -> str:
    cleaned = text.lower().strip().rstrip(".?!, ")
    for prefix in _WAKE_PREFIXES:
        if cleaned == prefix:
            return ""
        if cleaned.startswith(prefix + " "):
            return cleaned[len(prefix):].strip()
    return cleaned


def route_command(text: str):
    """
    Analyzes input text and routes it to a specific intent if it's a clear command.
    
    Returns:
        tuple: (intent, parameters) or (None, None) if LLM is needed.
    """
    text = _strip_wake_prefix(text)
    text_clean = text.rstrip(".?!, ").strip()

    # 0. Intent: greeting — hi, hello, hi jarvis, good morning, etc.
    if not text_clean or text_clean == "jarvis":
        return "greeting", {}
    if any(k == text_clean for k in ["hi", "hello", "hey", "jarvis", "wake up"]):
        return "greeting", {}
    if _GREETING_RE.match(text_clean):
        return "greeting", {}

    # Matches: "open whatsapp and search for vaasavi and send message hi iam jarvis"
    wa_search_msg = re.search(
        r"(?:open\s+)?whatsapp\s+(?:and\s+)?search\s+(?:for\s+)?(.+?)\s+(?:and\s+)?send\s+message\s+[\"']?(.+)[\"']?\s*$",
        text,
    )
    if wa_search_msg:
        return "send_whatsapp", {
            "name": wa_search_msg.group(1).strip(),
            "message": wa_search_msg.group(2).strip().strip("\"'")
        }

    # Matches: "open whatsapp and search [for] laxman", "whatsapp search [for] laxman"
    wa_search = re.search(r"(?:open\s+)?whatsapp\s+(?:and\s+)?search\s+(?:for\s+)?([a-zA-Z\s]+)", text)
    if wa_search:
        return "send_whatsapp", {
            "name": wa_search.group(1).strip(),
            "message": ""
        }

    # Matches: "send message to Rahul hello", "message Rahul hello"
    msg_match = re.search(r"(?:send message to|message)\s+([a-zA-Z\s]+?)\s+(.+)", text)
    if msg_match:
        return "send_whatsapp", {
            "name": msg_match.group(1).strip(),
            "message": msg_match.group(2).strip()
        }

    # 1b. Music control (transport + music-specific volume — before system volume)
    music_vol_set = re.search(
        r"(?:set\s+)?music\s+volume\s+to\s+(\d{1,3})|^music\s+volume\s+(\d{1,3})$",
        text_clean,
    )
    if music_vol_set:
        level = int(music_vol_set.group(1) or music_vol_set.group(2))
        return "music_control", {"action": "volume_set", "level": level}

    music_vol_up = re.search(
        r"(?:increase|raise|turn\s+up)\s+(?:the\s+)?music\s+volume(?:\s+by\s+(\d{1,3}))?"
        r"|music\s+volume\s+up(?:\s+by\s+(\d{1,3}))?",
        text,
    )
    if music_vol_up:
        amount = int(music_vol_up.group(1) or music_vol_up.group(2) or 10)
        return "music_control", {"action": "volume_up", "amount": amount}

    music_vol_down = re.search(
        r"(?:decrease|reduce|lower|turn\s+down)\s+(?:the\s+)?music\s+volume(?:\s+by\s+(\d{1,3}))?"
        r"|music\s+volume\s+down(?:\s+by\s+(\d{1,3}))?",
        text,
    )
    if music_vol_down:
        amount = int(music_vol_down.group(1) or music_vol_down.group(2) or 10)
        return "music_control", {"action": "volume_down", "amount": amount}

    if text_clean in {"mute music", "mute the music"}:
        return "music_control", {"action": "mute"}

    if text_clean in {"unmute music", "unmute the music"}:
        return "music_control", {"action": "unmute"}

    if re.search(r"(?:is\s+music\s+playing|music\s+status|what(?:'s| is)\s+playing)", text):
        return "music_control", {"action": "status"}

    if text_clean in {"stop music", "stop the music", "stop playback", "stop song"}:
        return "music_control", {"action": "stop"}

    if text_clean in {"pause music", "pause the music", "pause song", "pause playback"}:
        return "music_control", {"action": "pause"}

    if text_clean in {"resume music", "resume the music", "continue music", "unpause music"}:
        return "music_control", {"action": "resume"}

    if text_clean in {"restart music", "replay music", "play again"}:
        return "music_control", {"action": "restart"}

    # 1c. System volume control (Windows master 0–100, not music)
    if "music" not in text:
        set_vol = re.search(
            r"^set\s+(?:the\s+)?volume\s+(?:at|to)\s+(\d{1,3})$"
            r"|^(?:set\s+)?volume\s+to\s+(\d{1,3})$",
            text_clean,
        )
        if set_vol:
            level = int(set_vol.group(1) or set_vol.group(2))
            return "volume_control", {"action": "set", "level": level}

        inc_vol = re.search(
            r"(?:increase|raise|turn\s+up)\s+(?:the\s+)?volume(?:\s+by\s+(\d{1,3}))?"
            r"|^volume\s+up(?:\s+by\s+(\d{1,3}))?$",
            text,
        )
        if inc_vol:
            amount = inc_vol.group(1) or inc_vol.group(2) or 10
            return "volume_control", {"action": "up", "amount": int(amount)}

        dec_vol = re.search(
            r"(?:decrease|reduce|lower|turn\s+down)\s+(?:the\s+)?volume(?:\s+by\s+(\d{1,3}))?"
            r"|^volume\s+down(?:\s+by\s+(\d{1,3}))?$",
            text,
        )
        if dec_vol:
            amount = dec_vol.group(1) or dec_vol.group(2) or 10
            return "volume_control", {"action": "down", "amount": int(amount)}

        if text_clean in {"mute", "mute volume", "mute audio"}:
            return "volume_control", {"action": "mute"}

        if text_clean in {"unmute", "unmute volume", "unmute audio"}:
            return "volume_control", {"action": "unmute"}

        if re.search(r"(?:what(?:'s| is)\s+(?:the\s+)?volume|current volume|how loud)", text):
            return "volume_control", {"action": "get"}

    # 2. Music playback (local / spotify / youtube music / youtube search)
    local_music_cmds = {
        "play music", "play the music", "play some music", "start music", "put on music",
    }
    if text_clean in local_music_cmds:
        return "play_local_music", {}

    spotify_match = re.search(
        r"(?:play\s+(.+?)\s+on\s+spotify|spotify\s+(?:play\s+)?(.+)|play\s+(.+?)\s+(?:from|using)\s+spotify)",
        text,
    )
    if spotify_match:
        song = (spotify_match.group(1) or spotify_match.group(2) or spotify_match.group(3) or "").strip()
        if song:
            return "play_spotify", {"song": song}

    ytm_match = re.search(
        r"play\s+(.+?)\s+on\s+(?:youtube\s+music|yt\s+music)|(?:youtube\s+music|yt\s+music)\s+(?:play\s+)?(.+)",
        text,
    )
    if ytm_match:
        song = (ytm_match.group(1) or ytm_match.group(2) or "").strip()
        if song:
            return "play_youtube_music", {"song": song}

    yt_search_match = re.search(
        r"play\s+(.+?)\s+on\s+youtube|youtube\s+(?:play\s+)?(.+)|play\s+(.+?)\s+(?:from|using)\s+youtube",
        text,
    )
    if yt_search_match:
        song = (yt_search_match.group(1) or yt_search_match.group(2) or yt_search_match.group(3) or "").strip()
        if song and song.lower() not in {"music", "some music"}:
            return "play_youtube_search", {"song": song}

    # 4. Intent: open_app
    # Matches: "open chrome", "open spotify"
    open_match = re.search(r"open\s+([a-zA-Z0-9\-\.\s]+)", text)
    if open_match:
        app_name = open_match.group(1).replace(" please", "").strip()
        # Only accept if it looks like a simple app name
        if len(app_name.split()) <= 3 and " and " not in app_name:
            return "open_app", {"app": app_name}

    # 4b. Intent: time
    if re.search(r"what(?:'s| is)\s+(?:the\s+)?time\b", text):
        return "time", {}

    # 5. Intent: news / headlines (must be checked BEFORE search_browser)
    if any(k in text for k in ["news", "headlines", "latest news", "what's happening", "top stories", "news summary", "headlines summary", "read summary", "summary"]):
        return "news", {}

    # 6. Intent: search_browser
    # Matches: "search for newton's law", "google newton's law"
    search_match = re.search(r"(?:search\s+(?:for\s+)?|google\s+)(.+)", text)
    if search_match:
        return "search_browser", {"query": search_match.group(1).strip()}

    # 7. Intent: cancel_task
    # Matches: "stop", "cancel", "stop music", "cancel playing"
    if any(k in text for k in ["stop", "cancel", "shut up", "be quiet"]):
        # Determine what to cancel
        if any(w in text for w in ["music", "song", "audio", "playback"]):
            return "cancel_task", {"task_type": "music"}
        elif any(w in text for w in ["message", "whatsapp", "text"]):
            return "cancel_task", {"task_type": "messaging"}
        elif any(w in text for w in ["search", "browser", "google"]):
            return "cancel_task", {"task_type": "search"}
        else:
            # Cancel all
            return "cancel_task", {"task_type": "all"}

    # 8. Intent: joke
    if any(k in text for k in ["joke", "make me laugh", "say something funny", "tell me a joke"]):
        return "joke", {"style": "short, witty"}

    # 9. Intent: qa (General Knowledge)
    if re.search(r"^(what is|what are|who is|who are|how do|tell me about|explain|why is|why are|where is|when did|what's|who's)\b", text):
        return "qa", {"query": text}

    # 9. Intent: introduce / who are you
    if any(k in text for k in ["introduce yourself", "who are you", "what are you", "tell me about yourself"]):
        return "intro", {}

    # 8b. Intent: capabilities — what can you do
    if any(k in text for k in ["what can you do", "what are your capabilities", "what do you do", "your abilities", "your functions", "help me", "capabilities"]):
        return "capabilities", {}

    # 9. Intent: focus_window (user explicitly asks to return)
    if any(k in text for k in ["focus", "come back", "return to app", "bring back"]):
        return "focus_window", {}

    # 10. Intent: web_agent — autonomous click/browse/automate tasks
    agent_triggers = [
        "automate", "do it for me", "go to website", "browse to",
        "click on", "open and", "fill in", "type into", "navigate to",
        "agent do", "run agent", "do the task", "complete the task",
        "autonomous", "bot do", "perform task",
    ]
    if any(k in text for k in agent_triggers):
        # Strip trigger phrase to get clean task description
        task = text
        for trigger in agent_triggers:
            task = task.replace(trigger, "").strip()
        return "web_agent", {"task": task or text}

    # Return None to signify that the LLM should handle this (vague/conversational)
    return None, None

if __name__ == "__main__":
    test_cases = [
        "send message to Rahul hello",
        "play music",
        "stop music",
        "reduce music volume by 3",
        "set music volume to 50",
        "music status",
        "reduce volume by 3",
        "set volume to 50",
        "mute music",
        "play lofi on youtube",
        "play despacito on youtube music",
        "play back in black on spotify",
        "open chrome",
        "search for python",
        "how are you?",  # Should be None
    ]
    for cmd in test_cases:
        intent, params = route_command(cmd)
        print(f"{cmd:<30} -> {intent}")
