"""
router.py — Fast Rule-based Intent Router
==========================================
Routes explicit commands directly to tool intents to avoid LLM latency.

Intent catalogue includes Puppeteer browser tools (zero LLM latency when matched):
  play_youtube_music, browser_scroll_test, search_browser, linkedin_browser_demo, ...
"""

import re
from difflib import get_close_matches

_WAKE_PREFIXES = (
    "hey jarvis", "hi jarvis", "hello jarvis",
    "wake up jarvis", "wake jarvis", "jarvis",
)
_GREETING_RE = re.compile(
    r"^(?:hi|hello|hey|good\s+(?:morning|afternoon|evening))"
    r"(?:\s+jarvis)?$",
    re.IGNORECASE,
)

_TIMEZONE_ALIASES = {
    "tokyo": "Asia/Tokyo", "japan": "Asia/Tokyo",
    "london": "Europe/London", "uk": "Europe/London",
    "new york": "America/New_York", "newyork": "America/New_York",
    "los angeles": "America/Los_Angeles", "california": "America/Los_Angeles",
    "dubai": "Asia/Dubai", "singapore": "Asia/Singapore",
    "mumbai": "Asia/Kolkata", "delhi": "Asia/Kolkata", "india": "Asia/Kolkata",
    "utc": "UTC", "gmt": "Etc/GMT",
}


def _strip_wake_prefix(text: str) -> str:
    cleaned = text.lower().strip().rstrip(".?!, ")
    # Common speech/transcription spelling of WhatsApp.
    cleaned = re.sub(
        r"\bwhat(?:s|ts|tz)?\s*app\b|\bwatsapp\b|\bwatsap\b|\bwhatsap\b",
        "whatsapp",
        cleaned,
    )
    for prefix in _WAKE_PREFIXES:
        if cleaned == prefix:
            return ""
        if cleaned.startswith(prefix + " "):
            return cleaned[len(prefix):].strip()
    return cleaned


def _split_contact_and_message(rest: str) -> tuple:
    """
    Split 'sathish hello from jarvis' into (contact, message).
    Prefer longest saved-contact match at the start; else first token = contact.
    """
    rest = (rest or "").strip().strip("\"'")
    if not rest:
        return "", ""

    # Phone number at start (with optional spaces/dashes)
    phone_m = re.match(r"(\+?\d[\d\s\-()]{7,}\d)\s+(.+)$", rest)
    if phone_m:
        num = re.sub(r"\s+", "", phone_m.group(1))
        return num, phone_m.group(2).strip().strip("\"'")

    # Known contacts (longest match first). Allow natural punctuation after a
    # contact name: "Satish, hi" / "Satish. Bye".
    contacts = {}
    try:
        from executor.automation import _load_whatsapp_contacts
        contacts = _load_whatsapp_contacts()
    except Exception:
        contacts = {}
    lower = rest.lower()
    best = ""
    for name in sorted(contacts.keys(), key=len, reverse=True):
        if lower == name or re.match(rf"^{re.escape(name)}(?:\s|[,.:;-])", lower):
            if len(name) > len(best):
                best = name
    if best:
        # The separator is often spoken as a pause and transcribed as a dot:
        # "Satish. Bye" should produce "Bye", not ". Bye".
        msg = rest[len(best):].lstrip(" \t,.:;-").strip("\"'")
        msg = _strip_trailing_contact(msg, best)
        return best, msg

    # Tolerate common STT spelling differences (for example Satish/Sathish)
    # while still requiring one unique saved-contact match.
    first_token = re.split(r"[\s,.:;-]+", lower, maxsplit=1)[0]
    fuzzy = get_close_matches(first_token, contacts.keys(), n=3, cutoff=0.55)
    if fuzzy:
        unique_names = set(fuzzy)
        if len(unique_names) == 1 or _same_saved_number(contacts, fuzzy):
            name = fuzzy[0]
            msg = rest[len(first_token):].lstrip(" \t,.:;-").strip("\"'")
            msg = _strip_trailing_contact(msg, name)
            return name, msg

    # Default: first word = contact, remainder = message
    parts = rest.split(None, 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], _strip_trailing_contact(parts[1].strip().strip("\"'"), parts[0])


def _same_saved_number(contacts: dict, names: list) -> bool:
    phones = {str(contacts.get(n, "")).strip() for n in names if contacts.get(n)}
    return len(phones) == 1


def _strip_trailing_contact(message: str, contact: str) -> str:
    """Drop an STT echo of the contact name at the end: 'hi. satish' -> 'hi'."""
    if not message or not contact:
        return message
    cleaned = re.sub(
        rf"[\s,.:;-]+{re.escape(contact)}\s*$",
        "",
        message,
        flags=re.IGNORECASE,
    ).strip(" \t,.:;-\"'")
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

    # 0b. "Wake up. Daddy's home." — garage music + volume 50 + time-based welcome
    if re.search(
        r"wake\s*up\s*[.,!]?\s*daddy'?s?\s*home"
        r"|daddy'?s?\s*home"
        r"|wake\s*up[.,]?\s*daddy"
        r"|daddys?\s*home",
        text_clean,
        re.IGNORECASE,
    ):
        return "daddys_home", {"volume": 50}

    # ── WhatsApp confirm / cancel — ONLY when a draft is awaiting send ──
    try:
        from brain.memory import get_memory
        _pending_wa = get_memory("pending_whatsapp") or {}
    except Exception:
        _pending_wa = {}
    if isinstance(_pending_wa, dict) and _pending_wa.get("awaiting_confirm"):
        if re.fullmatch(
            r"(?:yes|yeah|yep|yup|ok|okay|sure|send|send\s+it|send\s+the\s+message|"
            r"go\s+ahead|confirm|do\s+it|proceed|please\s+send|green\s+light|"
            r"yes\s+send(?:\s+it)?|ok\s+send(?:\s+it)?)",
            text_clean,
            flags=re.IGNORECASE,
        ):
            return "confirm_whatsapp_send", {}
        if re.fullmatch(
            r"(?:no|nope|cancel|don'?t|don'?t\s+send|do\s+not\s+send|stop|"
            r"never\s*mind|nevermind|abort|discard|no\s+send)",
            text_clean,
            flags=re.IGNORECASE,
        ):
            return "cancel_whatsapp_send", {}

    # An automation failure leaves the request in memory so the user can say
    # "try again" without repeating a long voice command.
    retry_request = None
    try:
        from brain.memory import get_memory
        retry_request = get_memory("last_whatsapp_request")
    except Exception:
        pass
    if retry_request and re.search(
        r"\b(?:try\s+again|retry|resend|send\s+(?:it\s+)?again|one\s+more\s+time)\b",
        text_clean,
        re.IGNORECASE,
    ):
        return "send_whatsapp", {
            "name": retry_request.get("name", ""),
            "number": retry_request.get("number", ""),
            "message": retry_request.get("message", ""),
        }

    # Phone number pattern: +91 85199 29108 / 8519929108 / +918519929108
    _phone = r"(\+?\d[\d\s\-()]{7,}\d)"

    # "send message to +91 85199 29108 hello" / "message +918519929108 hi"
    msg_num = re.search(
        rf"(?:send\s+(?:a\s+)?message\s+to|message|whatsapp|text)\s+{_phone}\s+(.+)",
        text,
        re.IGNORECASE,
    )
    if msg_num:
        return "send_whatsapp", {
            "number": re.sub(r"\s+", "", msg_num.group(1).strip()),
            "name": re.sub(r"\s+", "", msg_num.group(1).strip()),
            "message": msg_num.group(2).strip().strip("\"'"),
        }

    # "open whatsapp and search for +91... and send message hi"
    wa_search_msg_num = re.search(
        rf"(?:open\s+)?whatsapp\s+(?:and\s+)?search\s+(?:for\s+)?{_phone}\s+"
        r"(?:and\s+)?send\s+(?:a\s+)?message\s+[\"']?(.+)[\"']?\s*$",
        text,
        re.IGNORECASE,
    )
    if wa_search_msg_num:
        return "send_whatsapp", {
            "number": re.sub(r"\s+", "", wa_search_msg_num.group(1).strip()),
            "name": re.sub(r"\s+", "", wa_search_msg_num.group(1).strip()),
            "message": wa_search_msg_num.group(2).strip().strip("\"'"),
        }

    # Natural spoken forms: "open WhatsApp and send Satish hi" and
    # "WhatsApp send Satish hi". Resolve the contact from the saved contacts
    # list so the message body can contain multiple words.
    wa_direct_send = re.search(
        r"(?:open\s+)?whatsapp\s+(?:and\s+)?send\s+"
        r"(?:a\s+)?(?:message\s+)?(?:to\s+)?(.+)$",
        text,
        re.IGNORECASE,
    )
    if wa_direct_send:
        contact, message = _split_contact_and_message(wa_direct_send.group(1))
        if contact:
            return "send_whatsapp", {"name": contact, "message": message}

    # "search for Satish and send him hi" is still an explicit WhatsApp
    # request when it names a saved contact; don't let it fall through to the
    # generic web-search LLM route.
    wa_search_then_send = re.search(
        r"(?:open\s+)?(?:whatsapp\s+and\s+)?search\s+for\s+(.+?)\s+"
        r"and\s+send\s+(?:him|her|a\s+message\s+to)\s+(.+)$",
        text,
        re.IGNORECASE,
    )
    if wa_search_then_send:
        contact = wa_search_then_send.group(1).strip().strip(".,:;- ")
        message = wa_search_then_send.group(2).strip().strip("\"'")
        if contact and message:
            return "send_whatsapp", {"name": contact, "message": message}

    # Compact spoken form: "send Satish hi" / "text Sathish hello".
    # Only claim this route when the first part resolves to a saved contact;
    # ordinary "send ..." requests remain available to the general router.
    compact_send = re.match(r"^(?:send|text)\s+(.+)$", text_clean, re.IGNORECASE)
    if compact_send:
        contact, message = _split_contact_and_message(compact_send.group(1))
        try:
            from executor.automation import _load_whatsapp_contacts
            saved_contacts = _load_whatsapp_contacts()
        except Exception:
            saved_contacts = {}
        if contact.lower() in saved_contacts and message:
            return "send_whatsapp", {"name": contact, "message": message}

    # Matches: "open whatsapp and search for vaasavi and send message hi iam jarvis"
    # Prefer resolving name→number later; still pass name for contact lookup
    wa_search_msg = re.search(
        r"(?:open\s+)?whatsapp\s+(?:and\s+)?search\s+(?:for\s+)?(.+?)\s+(?:and\s+)?send\s+(?:a\s+)?message\s+[\"']?(.+)[\"']?\s*$",
        text,
        re.IGNORECASE,
    )
    if wa_search_msg:
        target = wa_search_msg.group(1).strip()
        params = {"message": wa_search_msg.group(2).strip().strip("\"'")}
        if re.search(r"\d{8,}", target):
            params["number"] = re.sub(r"\s+", "", target)
            params["name"] = params["number"]
        else:
            params["name"] = target
        return "send_whatsapp", params

    # Matches: "open whatsapp and search [for] +91..." / "open whatsapp for sathish"
    wa_search = re.search(
        r"(?:open\s+)?whatsapp\s+(?:(?:and\s+)?search\s+(?:for\s+)?|for\s+|to\s+)(.+)$",
        text,
        re.IGNORECASE,
    )
    if wa_search:
        target = wa_search.group(1).strip().rstrip(".!?")
        if target.lower() not in ("app", "desktop", "application"):
            if re.search(r"\d{8,}", target):
                num = re.sub(r"\s+", "", target)
                return "send_whatsapp", {"number": num, "name": num, "message": ""}
            return "send_whatsapp", {"name": target, "message": ""}

    # Bare "whatsapp sathish" — never treat "and send ..." as a contact name.
    wa_bare = re.search(r"(?:open\s+)?whatsapp\s+(.+)$", text, re.IGNORECASE)
    if wa_bare:
        target = wa_bare.group(1).strip().rstrip(".!?")
        if (
            target.lower() not in ("app", "desktop", "application")
            and not re.match(r"(?:and\s+)?(?:send|search)\b", target, re.I)
        ):
            if re.search(r"\d{8,}", target):
                num = re.sub(r"\s+", "", target)
                return "send_whatsapp", {"number": num, "name": num, "message": ""}
            return "send_whatsapp", {"name": target, "message": ""}

    # "send message to sathish: hello" / "whatsapp message to X: body"
    msg_colon = re.search(
        r"(?:whatsapp\s+)?(?:send\s+)?message\s+to\s+(.+?)\s*[:\-]\s*(.+)$",
        text,
        re.IGNORECASE,
    )
    if msg_colon:
        target = msg_colon.group(1).strip()
        body = msg_colon.group(2).strip().strip("\"'")
        if re.search(r"\d{8,}", target):
            num = re.sub(r"\s+", "", target)
            return "send_whatsapp", {"number": num, "name": num, "message": body}
        return "send_whatsapp", {"name": target, "message": body}

    # "send message to Rahul hello" / "message Rahul hello"
    msg_match = re.search(
        r"(?:send\s+(?:a\s+)?message\s+to|message)\s+(.+)$",
        text,
        re.IGNORECASE,
    )
    if msg_match:
        contact, body = _split_contact_and_message(msg_match.group(1))
        if contact and body:
            params = {"name": contact, "message": body}
            if re.search(r"\d{8,}", contact):
                params["number"] = re.sub(r"\s+", "", contact)
            return "send_whatsapp", params

    # "send hi to sathish" / "send hello to sathish on whatsapp"
    # (message first, then "to" name) — must NOT match "send message to …"
    wa_to_name = re.search(
        r"^(?:send|text)\s+(?!message\b)[\"']?(.+?)[\"']?\s+to\s+"
        r"([A-Za-z][A-Za-z\s]*?)"
        r"(?:\s+on\s+whatsapp)?$",
        text_clean,
        re.IGNORECASE,
    )
    if wa_to_name:
        return "send_whatsapp", {
            "name": wa_to_name.group(2).strip(),
            "message": wa_to_name.group(1).strip().strip("\"'"),
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
    # Default local garage track (project folder) — must run BEFORE generic "play <song>"
    local_music_cmds = {
        "play music", "play the music", "play some music", "start music", "put on music",
        "play garage music", "play the garage music", "play garage", "garage music",
        "start garage music", "put on garage music", "play local music",
        "play my music", "play default music",
    }
    if text_clean in local_music_cmds or re.fullmatch(
        r"(?:can\s+you\s+(?:just\s+)?|please\s+|just\s+)?play\s+(?:the\s+|some\s+|my\s+)?"
        r"(?:garage\s+)?music(?:\s+please)?",
        text_clean,
        re.IGNORECASE,
    ):
        return "play_local_music", {"volume": 50}

    if re.search(
        r"\b(?:play|start|put\s+on)\s+(?:the\s+)?garage(?:\s+music)?\b",
        text_clean,
        re.IGNORECASE,
    ):
        return "play_local_music", {"volume": 50}

    spotify_match = re.search(
        r"(?:play\s+(.+?)\s+on\s+spotify|spotify\s+(?:play\s+)?(.+)|play\s+(.+?)\s+(?:from|using)\s+spotify)",
        text,
    )
    if spotify_match:
        song = (spotify_match.group(1) or spotify_match.group(2) or spotify_match.group(3) or "").strip()
        if song:
            return "play_spotify", {"song": song}

    # YouTube / YouTube Music — tolerate STT typos: yotube, utube, you tube, yt music
    _YT = r"(?:you\s*tube|youtube|yotube|utube|yt)"
    _YTM = rf"(?:{_YT}\s*music|yt\s*music|y\s*t\s*music)"

    ytm_match = re.search(
        rf"(?:can\s+you\s+(?:just\s+)?|please\s+|just\s+)?play\s+(.+?)\s+on\s+{_YTM}"
        rf"|{_YTM}\s+(?:play\s+)?(.+)"
        rf"|play\s+(.+?)\s+(?:from|using|via)\s+{_YTM}",
        text,
        re.IGNORECASE,
    )
    if ytm_match:
        song = (ytm_match.group(1) or ytm_match.group(2) or ytm_match.group(3) or "").strip()
        song = re.sub(r"\s+(?:please|now|for\s+me)$", "", song, flags=re.I).strip(" .,!?\"'")
        # Strip accidental platform words left in the capture
        song = re.sub(rf"\s+on\s+{_YTM}$", "", song, flags=re.I).strip()
        if song and song.lower() not in {"music", "some music", "a song"}:
            return "play_youtube_music", {"song": song}

    yt_search_match = re.search(
        rf"(?:can\s+you\s+(?:just\s+)?|please\s+|just\s+)?play\s+(.+?)\s+on\s+{_YT}(?!\s*music)"
        rf"|{_YT}(?!\s*music)\s+(?:play\s+)?(.+)"
        rf"|play\s+(.+?)\s+(?:from|using|via)\s+{_YT}(?!\s*music)",
        text,
        re.IGNORECASE,
    )
    if yt_search_match:
        song = (yt_search_match.group(1) or yt_search_match.group(2) or yt_search_match.group(3) or "").strip()
        song = re.sub(r"\s+(?:please|now|for\s+me)$", "", song, flags=re.I).strip(" .,!?\"'")
        if song and song.lower() not in {"music", "some music"}:
            # Prefer Music service for song-like queries
            return "play_youtube_music", {"song": song}

    # Generic "play <song>" / "can you just play <song>" -> YouTube Music via Puppeteer
    generic_play = re.search(
        r"^(?:can\s+you\s+(?:just\s+)?|please\s+|just\s+)?play\s+(.+)$",
        text_clean,
        re.IGNORECASE,
    )
    if generic_play:
        song = generic_play.group(1).strip()
        # Drop trailing platform words if STT mangled "on youtube music"
        song = re.sub(
            rf"\s+on\s+(?:{_YTM}|{_YT}|spotify)\s*$",
            "",
            song,
            flags=re.I,
        ).strip(" .,!?\"'")
        blocked = {
            "music", "some music", "the music", "a song", "something",
            "notepad", "chrome", "browser", "spotify", "youtube",
        }
        if song and song.lower() not in blocked and len(song) >= 2:
            return "play_youtube_music", {"song": song}

    # 4. Intent: open_app
    # Matches: "open chrome", "open spotify"
    open_match = re.search(r"open\s+([a-zA-Z0-9\-\.\s]+)", text)
    if open_match:
        app_name = open_match.group(1).replace(" please", "").strip()
        # Only accept if it looks like a simple app name
        if len(app_name.split()) <= 3 and " and " not in app_name:
            return "open_app", {"app": app_name}

    # 4a. Intent: calculator — tolerate filler words between the request and
    # the arithmetic, e.g. "calculate and zilk semi what is 57 plus 85".
    calc_match = re.search(
        r"\b(\d+(?:\.\d+)?)\s*(plus|\+|minus|-|times|\*|x|"
        r"multiplied\s+by|divided\s+by|/|over)\s*"
        r"(\d+(?:\.\d+)?)\b",
        text,
        re.IGNORECASE,
    )
    if calc_match and re.search(r"\b(?:calculate|compute|what\s+is|how\s+much)\b", text, re.I):
        return "calculate", {
            "left": calc_match.group(1),
            "operator": calc_match.group(2).lower(),
            "right": calc_match.group(3),
        }

    # 4b. Intent: time — before news (LLM often confuses "right now" with headlines)
    time_match = re.search(
        r"\b(?:what(?:'s|\s+is)\s+(?:the\s+)?time|what\s+time\s+is\s+it|current\s+time|tell\s+me\s+the\s+time)\b",
        text,
        re.IGNORECASE,
    )
    if time_match:
        timezone = None
        location = re.search(r"\b(?:in|at|for)\s+([a-zA-Z]+(?:\s+[a-zA-Z]+)?)\b", text, re.I)
        if location:
            city = location.group(1).lower().strip()
            timezone = _TIMEZONE_ALIASES.get(city)
        return "time", {"timezone": timezone} if timezone else {}

    # 5. Intent: news / headlines (must be checked BEFORE search_browser)
    if any(k in text for k in ["news", "headlines", "latest news", "what's happening", "top stories", "news summary", "headlines summary", "read summary"]):
        return "news", {}

    # 6. Intent: search_browser
    # Matches: "search for newton's law", "google newton's law"
    search_match = re.search(r"(?:search\s+(?:for\s+)?|google\s+)(.+)", text)
    if search_match:
        return "search_browser", {"query": search_match.group(1).strip()}

    # 7. Intent: cancel_task
    # Matches: "stop", "cancel", "stop music", "cancel playing"
    # Uses word-boundary match so "bus stop" or "don't stop the music" don't trigger
    if re.search(r"^(?:stop|cancel|shut\s+up|be\s+quiet)\b", text_clean):
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
    # Word-boundary match so "joker" or "no joke" don't trigger
    if re.search(r"\btell\s+me\s+a\s+joke\b|\bmake\s+me\s+laugh\b|\bsay\s+something\s+funny\b|^joke$", text_clean):
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

    # 10a. Puppeteer browser automation shortcuts
    # LinkedIn one-shot demo: scroll → Spotify login → YouTube Back in Black
    if re.search(
        r"linkedin\s*(browser\s*)?demo|browser\s*demo|demo\s*browser|"
        r"full\s*browser\s*demo|puppeteer\s*demo|run\s*the\s*demo",
        text,
    ):
        return "linkedin_browser_demo", {}
    if re.search(r"\b(log\s*in|login|sign\s*in)\b.*\bspotify\b|\bspotify\b.*\b(log\s*in|login|sign\s*in)\b", text):
        return "spotify_login", {}
    if re.search(r"scroll\s*(speed\s*)?test|test\s*scroll", text):
        return "browser_scroll_test", {}
    if re.search(r"\bnavigate to\s+(https?://\S+)", text):
        return "browser_navigate", {"url": re.search(r"navigate to\s+(https?://\S+)", text).group(1)}
    if re.search(r"\b(open|go to)\s+(https?://\S+)", text):
        return "browser_navigate", {"url": re.search(r"(https?://\S+)", text).group(1)}

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
