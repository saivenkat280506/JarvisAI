"""Small, topic-aware notebook for live browser research sessions."""

from __future__ import annotations

import re
from datetime import datetime

from services.runtime_state import flags

_STOP = {
    "about", "who", "what", "when", "where", "why", "how", "is", "are",
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "did",
    "does", "do", "his", "her", "their", "it", "this", "that", "tell",
    "me", "can", "you", "just", "please", "latest", "current", "news",
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", (text or "").lower()) if t not in _STOP}


def _related(query: str, topic: str) -> bool:
    q = _tokens(query)
    t = _tokens(topic)
    if not topic or not q or not t:
        return bool(topic and re.search(r"\b(?:he|she|they|it|that|this|his|her|their)\b", query, re.I))
    if q & t:
        return True
    return bool(re.search(r"\b(?:he|she|they|it|that|this|his|her|their|also|more|again)\b", query, re.I))


def start_or_continue(query: str) -> dict:
    """Start a clean notebook for a new subject and return its current state."""
    query = re.sub(r"\s+", " ", (query or "").strip())
    if not _related(query, flags.research_topic):
        flags.research_topic = query
        flags.research_entries = []
    return snapshot()


def add_entry(query: str, answer: str, sources: list[dict]) -> dict:
    start_or_continue(query)
    flags.research_entries.append({
        "number": len(flags.research_entries) + 1,
        "question": query,
        "answer": answer,
        "sources": sources[:5],
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    return snapshot()


def snapshot() -> dict:
    entries = list(flags.research_entries)
    lines = [f"J.A.R.V.I.S. RESEARCH NOTES — {flags.research_topic or 'Live research'}", ""]
    for entry in entries:
        lines.extend([
            f"Question {entry['number']}: {entry['question']}",
            f"Answer: {entry['answer']}",
            "Sources: " + ", ".join(s.get("title", "") for s in entry.get("sources", []) if s.get("title")),
            "",
        ])
    return {
        "topic": flags.research_topic,
        "count": len(entries),
        "entries": entries,
        "markdown": "\n".join(lines).strip(),
    }
