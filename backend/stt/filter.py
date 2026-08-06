"""
filter.py — Reject phantom STT results (Whisper hallucinations, echo, filler).
"""
import re

_PHANTOM_EXACT = {
    "thank you", "thank you.", "thanks", "thanks.", "you", "the", "bye",
    "okay", "ok", "uh", "um", "hmm", "ah", "oh", "huh", "yeah", "yes", "no",
    "thank you for watching", "thanks for watching", "subscribe",
    "you're welcome", "youre welcome",
    "subtitles by the amara.org community",
    "subtitles by the amara org community",
}

# Whisper training-data hallucinations (YouTube credits, websites, etc.)
_HALLUCINATION_MARKERS = (
    "amara.org",
    "amara org",
    "subtitles by",
    "subtitle by",
    "non-profit organization",
    "nonprofit organization",
    "community-driven model",
    "community driven model",
    "thanks for watching",
    "thank you for watching",
    "please subscribe",
    "like and subscribe",
    "visit www",
    "http://",
    "https://",
    "mbc news",
    "copyright",
    "all rights reserved",
)

_PHANTOM_PATTERNS = (
    r"^thank\s+you\b",
    r"^thanks\b",
    r"^subscribe\b",
    r"^for\s+watching\b",
    r"^subtitles?\s+by\b",
    r"\bamara\.?org\b",
)


def is_whisper_hallucination(text: str) -> bool:
    """Detect known Whisper garbage (subtitle credits, websites, etc.)."""
    if not text:
        return False
    lowered = text.lower()
    if any(marker in lowered for marker in _HALLUCINATION_MARKERS):
        return True
    for pattern in _PHANTOM_PATTERNS:
        if re.search(pattern, lowered):
            return True
    return False


def is_phantom_transcript(text: str, *, last_assistant: str = "") -> bool:
    """Return True if this transcript should be ignored (not a real user command)."""
    if not text:
        return True

    cleaned = re.sub(r"\s+", " ", text.lower().strip()).rstrip(".!?,")
    if len(cleaned) < 2:
        return True
    if cleaned in _PHANTOM_EXACT:
        return True
    if is_whisper_hallucination(cleaned):
        return True

    # Mic picked up JARVIS's own last reply (speaker echo) — word-boundary match only
    if last_assistant:
        assistant = last_assistant.lower()
        if re.search(rf"\b{re.escape(cleaned)}\b", assistant):
            return True

    return False