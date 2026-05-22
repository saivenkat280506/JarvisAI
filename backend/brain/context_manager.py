"""
context_manager.py — Contextual Intelligence
============================================
Combines current input with memory to create a context string for the LLM.
"""

from brain.memory import get_memory

def resolve_pronouns(text: str):
    """
    Directly resolves pronouns in the user input using memory.
    Returns the resolved text (for display) and resolved params (for action).
    """
    text_lower = text.lower()
    resolved_params = {}
    
    # Resolve "it", "again", "that song", "play it" -> last_song
    if any(k in text_lower for k in ["it", "again", "that song", "the same"]):
        last_song = get_memory("last_song")
        if last_song:
            resolved_params["song"] = last_song
            # Optionally replace text for LLM context
            text = text.replace("it", f"'{last_song}'")
            text = text.replace("again", f"'{last_song}' again")
    
    # Resolve "him", "her", "that person" -> last_contact
    if any(k in text_lower for k in ["him", "her", "that person"]):
        last_contact = get_memory("last_contact")
        if last_contact:
            resolved_params["name"] = last_contact
            text = text.replace("him", f"'{last_contact}'")
            text = text.replace("her", f"'{last_contact}'")
    
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
        
    return " | ".join(context_parts) if context_parts else "No recent context available."
