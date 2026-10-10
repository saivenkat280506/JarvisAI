"""Ollama client guard tests. No network, no model load."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.ollama_client import (
    _extract_text,
    _looks_like_leaked_thinking,
    _salvage_final_answer,
)

THINK_OPEN = "<" + "think>"
THINK_CLOSE = "<" + "/" + "think>"
THINKING_OPEN = "<" + "thinking>"
THINKING_CLOSE = "<" + "/" + "thinking>"


def check(label, cond):
    if not cond:
        raise AssertionError(label)
    print("OK", label)


LEAK = (
    "We are given the user's last message: \"This is not a joke actually.\" "
    "The user is responding to the previous JARVIS statement.\n"
    "We are to reply naturally in 1-3 sentences. Do not greet unless the user just said hello.\n"
    "Approach: Acknowledge their seriousness without being overly formal.\n"
    "Let's craft a response:\n"
    "Option: \"I appreciate you clarifying, sir.\"\n"
    "However, the instruction says: \"Reply naturally in 1-3 sentences.\"\n"
    "Final response:\n"
    "\"I take your seriousness to heart, sir. My remark was never a joke. I hope that helps.\"\n"
    "But note: the user says they didn't get any laugh."
)

UNSALVAGEABLE = (
    "We are given the user's message and must decide how to answer it.\n"
    "The instruction says to reply naturally in 1-3 sentences.\n"
    "Let's write something and stay in character."
)


def main():
    check("clean answer is not a leak", not _looks_like_leaked_thinking("The garage track is on, sir."))
    check("greeting is not a leak", not _looks_like_leaked_thinking("Good morning, sir. Systems are online."))
    check("let me think is a leak", _looks_like_leaked_thinking("Let me think about the user's question first."))
    check("we-are-given is a leak", _looks_like_leaked_thinking(LEAK))
    check("meta deliberation is a leak", _looks_like_leaked_thinking(UNSALVAGEABLE))

    salvaged = _salvage_final_answer(LEAK)
    check("salvage finds the settled answer", salvaged.startswith("I take your seriousness to heart"))
    check("salvage rejects meta text", not _salvage_final_answer(UNSALVAGEABLE))

    check(
        "extract rescues the answer",
        _extract_text({"message": {"content": LEAK}}) == salvaged,
    )
    check("extract drops unsalvageable leak", _extract_text({"message": {"content": UNSALVAGEABLE}}) == "")

    traced = f"Why the answer is simple.{THINK_OPEN}the user wants a joke{THINK_CLOSE} Music stopped, sir."
    check("extract strips the reasoning tag", _extract_text({"message": {"content": traced}}) == "Music stopped, sir.")

    unfinished = f"Working through it.{THINKING_OPEN} still deciding."
    check("unfinished trace is dropped", _extract_text({"message": {"content": unfinished}}) == "")

    closed_thinking = f"I checked the details.{THINKING_OPEN}private trace{THINKING_CLOSE} Music stopped, sir."
    check("thinking tags are stripped", _extract_text({"message": {"content": closed_thinking}}) == "Music stopped, sir.")

    check("extract passes clean text through",
          _extract_text({"message": {"content": "Successfully opened WhatsApp."}}) == "Successfully opened WhatsApp.")
    check("think=True keeps the trace",
          _extract_text({"message": {"content": traced}}, think=True) == traced)

    print("ALL OK")


if __name__ == "__main__":
    main()
