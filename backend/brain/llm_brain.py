"""
llm_brain.py — Fast intent router via the configured LLM
========================================================
Intent classification only (low latency). Long-form chat/QA uses the same
provider in command_processor._ollama_generate via llm.ollama_client.
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
from config import settings  # noqa: E402
from brain.llm_provider import PROVIDER, MODEL, ChatLLM, describe  # noqa: E402
from llm.prompts import load_prompt  # noqa: E402

# Fast model for tool routing (env JARVIS_ROUTER_MODEL overrides the shared model)
ROUTER_MODEL = os.environ.get("JARVIS_ROUTER_MODEL") or MODEL

llm = ChatLLM(
    temperature=0,
    max_tokens=120,
    model=ROUTER_MODEL,
)


# Compact prompt → fewer tokens → lower latency
SYSTEM_PROMPT = load_prompt("tool_planning.md")


def decide_action(user_input: str, context: str = ""):
    # Keep context tiny for speed
    ctx = (context or "")[-400:]
    prompt = f"Context:{ctx}\nUser:{user_input}\nReturn JSON:"
    try:
        response = llm.invoke([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ])
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
        if re.search(r"\b(?:what is|what are|who is|who are|tell me about|explain)\b", low):
            return {"intent": "qa", "parameters": {"query": user_input}}
        if "on the internet" in low or "on the web" in low or low.startswith("look up "):
            return {"intent": "smart_search", "parameters": {"query": user_input}}
        if "search" in low or "google" in low:
            return {"intent": "search_browser", "parameters": {"query": user_input}}
        return {"intent": "chat", "parameters": {"response": ""}}
