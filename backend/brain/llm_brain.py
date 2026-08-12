"""
llm_brain.py — Fast intent router via Groq llama-3.1-8b-instant
==============================================================
Intent classification only (low latency). Long-form chat/QA uses
llama-3.1-8b-instant in command_processor._groq_generate as well.
Reserve 70B only for rare heavy reasoning if ever needed.
"""

from __future__ import annotations

import json
import os
import re
import sys

from langchain_groq import ChatGroq

sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))
from config import settings

# Fast model for tool routing (~100–300ms on Groq)
ROUTER_MODEL = os.environ.get("JARVIS_ROUTER_MODEL", "llama-3.1-8b-instant")

llm = ChatGroq(
    temperature=0,
    model_name=ROUTER_MODEL,
    groq_api_key=settings.GROQ_API_KEY,
    max_tokens=120,
)

# Compact prompt → fewer tokens → lower latency
SYSTEM_PROMPT = """You map user text to ONE JARVIS tool intent. Return ONLY JSON:
{"intent":"...","parameters":{...}}

BROWSER (Puppeteer — preferred for web/music UI):
- play_youtube_music {"song":"..."}  — play any song (default for "play X")
- play_youtube_search {"song":"..."} — same as music (legacy)
- browser_scroll_test {} — "scroll test" / "scroll speed test"
- search_browser {"query":"..."} — "search for X" / "google X" (opens browser + scroll)
- browser_navigate {"url":"https://..."}
- browser_click {"selector":"..."} or {"text":"..."}
- browser_type {"selector":"...","text":"..."}
- browser_scroll {"pixels":350,"times":3,"delayMs":5000}
- browser_action {"action":"youtube_music_play|scroll_test|web_search|..."}
- linkedin_browser_demo {} — scroll then YouTube Music demo
- play_spotify {"song":"..."} — only if user says Spotify
- spotify_login {} — only if user says log in to Spotify

OTHER:
- open_app {"app":"..."}
- send_whatsapp {"name":"...","message":"...","number":"+91..."} — ALWAYS include phone number when known; WhatsApp searches by NUMBER only (never name) to avoid same-name confusion. Drafts message and asks user to confirm before send.
- confirm_whatsapp_send {} — user said yes/ok/send after a draft
- cancel_whatsapp_send {} — user said no/cancel after a draft
- play_local_music {} — "play music" / "play garage music" (local garage track)
- daddys_home {} — "wake up daddy's home" / "daddy's home"
- music_control / volume_control
- read_headlines {"query":"..."} / smart_search {"query":"..."}  (text only, no browser)
- chat / joke / intro / qa / cancel_task / web_agent

Rules: Prefer play_youtube_music for play requests. Prefer search_browser for "search/google in browser". Prefer smart_search for "what is". No markdown.
"""


def decide_action(user_input: str, context: str = ""):
    # Keep context tiny for speed
    ctx = (context or "")[-400:]
    prompt = f"{SYSTEM_PROMPT}\nContext:{ctx}\nUser:{user_input}\nJSON:"
    try:
        response = llm.invoke(prompt)
        content = (response.content or "").strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        # Extract first {...} if model adds chatter
        if "{" in content and "}" in content:
            content = content[content.index("{") : content.rindex("}") + 1]
        data = json.loads(content)
        if not isinstance(data, dict) or "intent" not in data:
            raise ValueError("bad shape")
        data.setdefault("parameters", {})
        if not isinstance(data["parameters"], dict):
            data["parameters"] = {}
        print(f"[LLM Brain] {ROUTER_MODEL} -> {data.get('intent')}")
        return data
    except Exception as e:
        print(f"[LLM Brain Error] {e}")
        # Safe browser-oriented fallback for ambiguous web-ish input
        low = user_input.lower()
        if "play" in low and (
            "youtube" in low or "yotube" in low or "utube" in low or "yt music" in low
            or low.startswith("play ") or "play " in low
        ):
            # Extract after last "play "
            song = user_input
            if "play " in low:
                song = user_input[low.index("play ") + 5 :].strip()
            song = re.sub(
                r"\s+on\s+(?:you\s*tube|youtube|yotube|utube|yt)(?:\s*music)?\s*$",
                "",
                song,
                flags=re.I,
            ).strip(" .,!?")
            if song:
                return {"intent": "play_youtube_music", "parameters": {"song": song}}
        if "search" in low or "google" in low:
            return {"intent": "search_browser", "parameters": {"query": user_input}}
        return {"intent": "chat", "parameters": {"response": ""}}
