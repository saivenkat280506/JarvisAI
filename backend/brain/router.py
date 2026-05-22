"""
router.py — Fast Rule-based Intent Router
==========================================
Routes explicit commands directly to tool intents to avoid LLM latency.

Intent catalogue:
  chat, send_whatsapp, play_youtube_music, open_app,
  search_browser, cancel_task,
  news, joke, intro, focus_window
"""

import re

def route_command(text: str):
    """
    Analyzes input text and routes it to a specific intent if it's a clear command.
    
    Returns:
        tuple: (intent, parameters) or (None, None) if LLM is needed.
    """
    text = text.lower().strip()
    text_clean = text.rstrip(".?!, ").strip()

    # 0. Intent: chat / greeting — delegate time-aware greeting to main.py
    if any(k == text_clean for k in ["hi", "hello", "hey", "jarvis", "wake up"]):
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

    # 2. Intent: play_youtube_music
    # Matches: "play song on youtube", "youtube play lofi", "play back in black"
    yt_match = re.search(r"play\s+(.+?)(?:\s+on youtube)?$|youtube\s+(?:play\s+)?(.+)", text)
    if yt_match:
        song = yt_match.group(1) or yt_match.group(2)
        return "play_youtube_music", {"song": song.strip()}

    # 3. Intent: open_app
    # Matches: "open chrome", "open spotify"
    open_match = re.search(r"open\s+([a-zA-Z0-9\-\.\s]+)", text)
    if open_match:
        app_name = open_match.group(1).replace(" please", "").strip()
        # Only accept if it looks like a simple app name
        if len(app_name.split()) <= 3 and " and " not in app_name:
            return "open_app", {"app": app_name}

    # 4. Intent: news / headlines (must be checked BEFORE search_browser)
    if any(k in text for k in ["news", "headlines", "latest news", "what's happening", "top stories", "news summary", "headlines summary", "read summary", "summary"]):
        return "news", {}

    # 5. Intent: search_browser
    # Matches: "search for newton's law", "google newton's law"
    search_match = re.search(r"(?:search\s+(?:for\s+)?|google\s+)(.+)", text)
    if search_match:
        return "search_browser", {"query": search_match.group(1).strip()}

    # 6. Intent: cancel_task
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

    # 7. Intent: joke
    if any(k in text for k in ["joke", "make me laugh", "say something funny", "tell me a joke"]):
        return "joke", {"style": "short, witty"}

    # 8. Intent: qa (General Knowledge)
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
        "play lofi on youtube",
        "open chrome",
        "search for python",
        "how are you?", # Should be None
    ]
    for cmd in test_cases:
        intent, params = route_command(cmd)
        print(f"{cmd:<30} -> {intent}")
