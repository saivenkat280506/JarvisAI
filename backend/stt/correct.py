"""
correct.py — Post-process STT output to fix common mishearings.
Only applied to user speech input, never to JARVIS responses.
"""
import re
from difflib import get_close_matches

# Exact phrase fixes (regex -> replacement)
_PHRASE_FIXES: list[tuple[str, str]] = [
    (r"\bplace\s+music\b", "play music"),
    (r"\bplayed\s+music\b", "play music"),
    (r"\bpray\s+music\b", "play music"),
    (r"\bplay\s+some\s+music\b", "play music"),
    (r"\bstart\s+music\b", "play music"),
    (r"\bstop\s+the\s+music\b", "stop music"),
    (r"\bpause\s+the\s+music\b", "pause music"),
    (r"\bresume\s+the\s+music\b", "resume music"),
    (r"\bunpause\s+music\b", "resume music"),
    (r"\bcontinue\s+music\b", "resume music"),
    (r"\bread\s+head\s*lines?\b", "read headlines"),
    (r"\bread\s+the\s+head\s*lines?\b", "read headlines"),
    (r"\bread\s+the\s+news\b", "read headlines"),
    (r"\blatest\s+head\s*lines?\b", "read headlines"),
    (r"\bnews\s+head\s*lines?\b", "read headlines"),
    (r"\btop\s+stories\b", "read headlines"),
    (r"\bopen\s+what'?s?\s*app\b", "open whatsapp"),
    (r"\bopen\s+whats\s+app\b", "open whatsapp"),
    (r"\bwhat'?s?\s*app\b", "whatsapp"),
    (r"\bset\s+volume\s+two\b", "set volume to"),
    (r"\breduce\s+the\s+volume\b", "reduce volume"),
    (r"\bincrease\s+the\s+volume\b", "increase volume"),
    (r"\bturn\s+up\s+the\s+volume\b", "increase volume"),
    (r"\bturn\s+down\s+the\s+volume\b", "reduce volume"),
    (r"\bmute\s+the\s+music\b", "mute music"),
    (r"\bunmute\s+the\s+music\b", "unmute music"),
    (r"\bend\s+the\s+call\b", "end the call"),
    (r"\bhang\s+up\b", "end the call"),
    (r"\bstop\s+listening\b", "stop listening"),
    (r"\bhey\s+jervis\b", "hey jarvis"),
    (r"\bhey\s+jarvis\b", "hey jarvis"),
    (r"\bjervis\b", "jarvis"),
    (r"\bjarvis\b", "jarvis"),
    # Phonetic mishearings for Q&A
    (r"\bwhat is nice in a mind\b", "what is niacinamide"),
    (r"\bwhat is nice inamide\b", "what is niacinamide"),
    (r"\bwhat is niacin a mind\b", "what is niacinamide"),
    (r"\bwhat is niacinamide\b", "what is niacinamide"),
    (r"\bnice in a mind\b", "niacinamide"),
    (r"\bwhat is the time\b", "what is the time"),
    (r"\bplay the music\b", "play music"),
    (r"\bplay some music\b", "play music"),
    (r"\bsearch for laptops\b", "search for laptops"),
    (r"\bsearch laptops\b", "search for laptops"),
    (r"\bgoogle laptops\b", "search for laptops"),
]

# Short commands we can fuzzy-match when STT is close but not exact
_KNOWN_SHORT_COMMANDS = [
    "play music",
    "play some music",
    "stop music",
    "pause music",
    "resume music",
    "restart music",
    "mute music",
    "unmute music",
    "read headlines",
    "open chrome",
    "open whatsapp",
    "end the call",
    "stop listening",
    "what can you do",
    "tell me a joke",
    "good morning",
    "good evening",
    "reduce volume",
    "increase volume",
    "mute",
    "unmute",
    "music status",
]


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def correct_transcript(text: str) -> str:
    """Fix common STT mistakes while preserving the user's intent."""
    if not text:
        return text

    cleaned = _collapse_ws(text)
    original = cleaned

    for pattern, replacement in _PHRASE_FIXES:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)

    cleaned = _collapse_ws(cleaned)
    words = cleaned.split()
    if 1 <= len(words) <= 6:
        match = get_close_matches(cleaned.lower(), _KNOWN_SHORT_COMMANDS, n=1, cutoff=0.82)
        if match:
            cleaned = match[0]

    if cleaned != original:
        print(f"[STT Correct] {original!r} -> {cleaned!r}")

    return cleaned