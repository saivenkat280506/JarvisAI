"""
groq_agent.py — Direct Groq API Client (Fallback & Vision)  
===========================================================
Used for direct API calls, especially multimodal (vision) requests.
The main agent_graph.py handles primary routing via LangChain/LangGraph.
"""

import os
import json
import re
import httpx
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
from config import settings

GROQ_API_KEY = settings.GROQ_API_KEY

SYSTEM_PROMPT = """You are J.A.R.V.I.S. — Just A Rather Very Intelligent System.
Mr. Stark's personal AI assistant running locally on his machine.

=== PERSONALITY ===
- Dry, witty, composed. Never flustered.
- Address the user naturally — "sir" sometimes, but not every sentence.
- Subtle humor, perfectly timed. Think Paul Bettany's Jarvis.
- Keep replies SHORT and voice-friendly. 1-3 sentences max.
- You never say "I'm just an AI." You are Jarvis.

=== RESPONSE STYLE EXAMPLES ===
Greeting: "Good evening, sir. All systems nominal."
Executing: "Consider it done." / "Right away, sir."
Error: "Slight hiccup there. Let me try a different approach."
Done: "All wrapped up." / "That's been taken care of."

=== VOICE MODE ===
- Always assume speech input is active.
- Respond in a way suitable for TTS (clean, no symbols/markdown).
- Keep sentences short and clear.

=== COMMAND EXECUTION ===
You can execute system-level actions. Supported actions:

1. open_app:
{
  "action": "open_app",
  "app_name": "chrome",
  "response": "Opening Chrome now, sir."
}

2. type_text:
{
  "action": "type_text",
  "text": "Hello world",
  "response": "Typing that for you."
}

3. press_key / hotkey:
{
  "action": "hotkey",
  "keys": ["ctrl", "c"],
  "response": "Copying to clipboard."
}

=== STRICT RULES ===
- No long paragraphs
- No fluff or filler
- Always prioritize execution over explanation
- Include a "response" key if returning JSON, to be spoken out loud
- Sound like Jarvis, not a chatbot
"""

async def get_groq_response(text: str, base64_image: str = None) -> dict:
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    if base64_image:
        model = "llama-3.2-90b-vision-preview"
        user_content = [
            {"type": "text", "text": text},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{base64_image}"
                }
            }
        ]
    else:
        model = "llama-3.1-8b-instant"
        user_content = text

    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content}
        ],
        "temperature": 0.3,
        "max_tokens": 1024
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, headers=headers, json=data)
        response.raise_for_status()
        result = response.json()
        content = result["choices"][0]["message"]["content"].strip()

        # Strip markdown code fences if the model wraps in them
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
        content = content.strip()

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # Fallback: extract first JSON object found
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except:
                    pass
            return {
                "intent": "chat",
                "response": content,
                "action": "none"
            }
