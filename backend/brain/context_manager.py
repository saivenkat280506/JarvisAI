"""
context_manager.py — Contextual Intelligence
============================================
Combines current input with memory to create a context string for the LLM.
"""

import re
from brain.memory import get_memory


def _replace_whole_word(text: str, word: str, replacement: str) -> str:
    """Replace *word* only when it appears as a whole word (not inside other words)."""
    return re.sub(rf"\b{re.escape(word)}\b", replacement, text, flags=re.IGNORECASE)


def resolve_pronouns(text: str):
    """
    Directly resolves pronouns in the user input using memory.
    Returns the resolved text (for display) and resolved params (for action).
    """
    text_lower = text.lower()
    resolved_params = {}

    # Resolve "it", "again", "that song", "play it" -> last_song
    # Use word-boundary check so "it" doesn't match inside "write", "bitcoin", etc.
    # Do not rewrite WhatsApp retries ("try again" / "send it again") as a song.
    if re.search(
        r"\b(?:try\s+again|resend|send\s+(?:it\s+)?again|one\s+more\s+time)\b",
        text_lower,
    ):
        pass
    elif re.search(r"\b(?:it|again|that song|the same)\b", text_lower):
        last_song = get_memory("last_song")
        if last_song:
            resolved_params["song"] = last_song
            text = _replace_whole_word(text, "it", f"'{last_song}'")
            text = _replace_whole_word(text, "again", f"'{last_song}' again")

    # Resolve "him", "her", "that person" -> last_contact
    # Use word-boundary check so "him" doesn't match "thimble", "her" doesn't match "where"/"other"
    if re.search(r"\b(?:him|her|that person)\b", text_lower):
        last_contact = get_memory("last_contact")
        if last_contact:
            resolved_params["name"] = last_contact
            text = _replace_whole_word(text, "him", f"'{last_contact}'")
            text = _replace_whole_word(text, "her", f"'{last_contact}'")
    
    return text, resolved_params

def get_current_context():
    """
    Builds a structured summary of recent activity to help the LLM resolve pronouns.
    """
    history = get_memory("history") or []
    last_contact = get_memory("last_contact")
    last_song = get_memory("last_song")
    
    context_parts = []
    if history:
        recent = history[-3:]
        context_parts.append(f"Recent activity: {', '.join(recent)}")
    if last_contact:
        context_parts.append(f"Last contacted person: {last_contact}")
    if last_song:
        context_parts.append(f"Last played song/artist: {last_song}")
    try:
        from memory.db import current_conversation_id, list_recent_messages, list_user_profile

        profile = list_user_profile()
        private_keys = ("password", "secret", "token", "credential", "api_key")
        profile_items = [
            f"{str(key)[:60]}={str(value)[:120]}"
            for key, value in profile.items()
            if not any(term in str(key).lower() for term in private_keys)
        ]
        if profile_items:
            context_parts.append("Saved profile: " + "; ".join(profile_items)[:400])

        recent = list_recent_messages(
            6, exclude_conversation_id=current_conversation_id()
        )
        if recent:
            lines = []
            for item in recent:
                who = "User" if item["role"] == "user" else "JARVIS"
                content = re.sub(r"\s+", " ", item["content"])
                lines.append(f"{who}: {content[:180]}")
            context_parts.append("Earlier conversation: " + " | ".join(lines)[:650])
    except Exception:
        pass
    try:
        from brain.memory_store import context_snippet
        snippet = context_snippet()
        if snippet:
            context_parts.append(snippet[:400])
    except Exception:
        pass
        
    return " | ".join(context_parts) if context_parts else "No recent context available."
