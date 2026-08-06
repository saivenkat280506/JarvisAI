"""
command_processor.py — Command Processing Pipeline for JARVIS
=============================================================
Processes transcribed text through router -> LLM -> tool execution.
Yields SSE-formatted payloads for the frontend stream.
"""

import asyncio
import json
import re
import uuid
import time as _time
from datetime import datetime

from services.event_bus import event_bus, BusEvent, EventType
from services.runtime_state import flags, SystemState
from services.voice_loop import set_state, _time_of_day
from services.websocket_manager import manager
from brain.llm_brain import decide_action
from brain.context_manager import get_current_context, resolve_pronouns
from brain.memory import add_to_history
from brain.personality import respond_success, respond_fail
from executor.tool_executor import execute_tool
from tts.hybrid_tts import speak_hybrid as speak
from config import settings

JARVIS_SYSTEM_PROMPT = """
You are J.A.R.V.I.S. — Just A Rather Very Intelligent System.
Personality: calm, precise, slightly formal, dry wit, voice-friendly.
Rules:
- Reply in 1-3 short sentences max.
- Never use markdown, bullet points, or symbols.
- Sound like Paul Bettany's Jarvis, not a chatbot.
- Address user as "sir" occasionally, not every sentence.
- Never open with Good morning, Good afternoon, or Good evening unless the user just greeted you.
- For factual questions, answer directly — start with the information, not a salutation.
"""

QA_SYSTEM_PROMPT = JARVIS_SYSTEM_PROMPT + """
You are answering a direct factual question in an ongoing conversation.
Do NOT greet the user. Do NOT say good morning, good afternoon, good evening, or hello.
Begin immediately with the answer.
"""

WAKE_PHRASES = {
    "jarvis", "hey jarvis", "hi jarvis", "hello jarvis",
    "wake up jarvis", "wake jarvis", "jarvis listen",
}


def _is_greeting_text(text: str) -> bool:
    from brain.router import route_command
    intent, _ = route_command(text)
    return intent == "greeting"

_CALL_END_PHRASES = (
    "end the call",
    "end call",
    "hang up",
    "stop voice",
    "exit voice",
    "close voice",
    "terminate call",
    "stop the call",
    "terminate yourself",
    "end the call jarvis",
    "go to sleep",
    "stop listening",
)

CONTROL_KEYWORDS = (
    "stop", "cancel", "pause", "resume", "volume", "mute", "unmute",
    "louder", "quieter", "increase", "decrease", "reduce", "lower", "raise",
)

_CALL_END_EXACT = frozenset(_CALL_END_PHRASES) | frozenset(
    f"jarvis {p}" for p in _CALL_END_PHRASES
) | frozenset(
    f"{p} jarvis" for p in _CALL_END_PHRASES
)


def _is_explicit_call_end(cmd_clean: str) -> bool:
    """Require an exact end-call phrase — not a substring inside a longer hallucination."""
    normalized = re.sub(r"\s+", " ", cmd_clean.strip().rstrip(".!?,"))
    return normalized in _CALL_END_EXACT


def _is_control_command(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in CONTROL_KEYWORDS)


def _strip_leading_greeting(text: str) -> str:
    """Remove redundant greetings/filler so Q&A answers don't repeat salutations."""
    cleaned = text.strip()
    patterns = (
        r"^(?:good\s+(?:morning|afternoon|evening),?\s*(?:sir)?[.!]?\s*)+",
        r"^(?:hello|hi|hey)(?:\s+there)?,?\s*(?:sir)?[.!]?\s*",
        r"^sir[,!.]?\s+",
        r"^here(?:'s| is)\s+(?:a\s+)?(?:brief\s+)?summary\s+of\s+(?:today'?s\s+)?(?:top\s+)?headlines[.:]?\s*",
    )
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned or text.strip()


async def _groq_generate(prompt: str, system: str = None, *, allow_greeting: bool = False) -> str:
    import httpx
    groq_key = settings.GROQ_API_KEY
    if not groq_key:
        return "I seem to have misplaced my API key, sir."

    now_dt = datetime.now()
    time_str = now_dt.strftime("%A, %B %d, %Y, %I:%M %p")
    if allow_greeting:
        time_context = (
            f"\n\nCurrent Context: The current local time is {time_str}. "
            "Make sure your greetings (Good morning/afternoon/evening) match this exactly."
        )
    else:
        time_context = (
            f"\n\nCurrent Context: The current local time is {time_str}. "
            "Do NOT greet the user. Do not say 'Good morning/afternoon/evening' or 'sir' at the start. "
            "Begin immediately with the requested content."
        )
    sys_msg = (system or JARVIS_SYSTEM_PROMPT) + time_context

    payload = {
        "model": "llama-3.1-8b-instant",
        "messages": [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.55,
        "max_tokens": 256,
    }
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}"},
                json=payload,
            )
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"].strip()
            return text if allow_greeting else _strip_leading_greeting(text)
    except Exception as exc:
        print(f"[Groq] Error: {exc}")
        return "I ran into a small issue there, sir."


async def _fetch_news_summary() -> str:
    import httpx
    news_key = settings.NEWS_API_KEY
    headlines = []

    if news_key:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(
                    "https://newsapi.org/v2/top-headlines",
                    params={"country": "us", "pageSize": 6, "apiKey": news_key},
                )
                data = r.json()
                headlines = [a["title"] for a in data.get("articles", []) if a.get("title")]
        except Exception as exc:
            print(f"[News] Fetch error: {exc}")

    if not headlines:
        try:
            import time
            cache_buster = int(time.time())
            rss_url = f"https://news.google.com/rss/search?q=top+stories&hl=en-US&gl=US&ceid=US:en&t={cache_buster}"
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(rss_url, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
                import xml.etree.ElementTree as ET
                root = ET.fromstring(r.text)
                headlines = [item.find('title').text for item in root.findall('.//item') if item.find('title') is not None][:6]
        except Exception as exc:
            print(f"[News] RSS fallback error: {exc}")

    if not headlines:
        return "I wasn't able to pull the latest headlines, sir. Network may be restricted."

    bullet_list = "\n".join(f"- {h}" for h in headlines[:3])
    prompt = (
        f"Here are today's top trending headlines:\n{bullet_list}\n\n"
        "The user asked for the latest news headlines summary. "
        "Provide a very brief, elegant, and snappy summary of the top 3 headlines. "
        "State only the main points in a natural, spoken conversational way. "
        "Do NOT greet the user. Do NOT say 'Good morning/afternoon/evening' or 'Here's a summary'. "
        "Start directly with the first headline. "
        "Do NOT elaborate on detailed backstories or add extra sentences for each story. "
        "Keep the entire spoken response short, clean, and under 12-15 seconds total. "
        "Do not use markdown, lists, or bullets."
    )
    summary = await _groq_generate(
        prompt,
        system=(
            "You are J.A.R.V.I.S. delivering a concise spoken news briefing. "
            "No greetings, no filler openers — jump straight into the headlines. "
            "Speak clearly and smoothly without markdown symbols."
        ),
        allow_greeting=False,
    )
    return _strip_leading_greeting(summary)


async def _speak_in_background(text: str, is_smart: bool, response_id: str):
    speak_epoch = flags.speak_epoch
    try:
        await set_state(SystemState.SPEAKING)
        if flags.speak_epoch != speak_epoch:
            return
        await speak(text, is_smart=is_smart, response_id=response_id)
    finally:
        if flags.speak_epoch != speak_epoch:
            await set_state(SystemState.IDLE)
        elif flags.continuous_voice_mode and not flags.stop_listen_trigger:
            await set_state(SystemState.IDLE_LISTENING)
        else:
            await set_state(SystemState.IDLE)


async def _speak_then_play_music(text: str, response_id: str):
    from executor.music_services import play_local_garage

    speak_epoch = flags.speak_epoch
    try:
        await set_state(SystemState.SPEAKING)
        if flags.speak_epoch != speak_epoch:
            return
        await speak(text, is_smart=False, response_id=response_id)
        if flags.speak_epoch != speak_epoch:
            return
        await asyncio.to_thread(play_local_garage)
    finally:
        if flags.speak_epoch != speak_epoch:
            await set_state(SystemState.IDLE)
        elif flags.continuous_voice_mode and not flags.stop_listen_trigger:
            await set_state(SystemState.IDLE_LISTENING)
        else:
            await set_state(SystemState.IDLE)


async def _push_chat(text: str, *, voice: bool):
    """Voice sessions use WebSocket; typed chat uses SSE only (avoid duplicate UI messages)."""
    if voice:
        await manager.broadcast_chat(text)


async def _handle_call_termination(*, voice: bool, response_id: str):
    """End the continuous voice-call session (Friday-style pre-graph intercept)."""
    from tts.pocket_tts import stop_speech
    from brain.personality import respond_cancel

    flags.continuous_voice_mode = False
    flags.stop_listen_trigger = True
    flags.voice_session_active = False
    stop_speech()
    flags.stop_event.set()
    await event_bus.emit(BusEvent(EventType.STOP))

    final_response = respond_cancel(success=True)
    await _push_chat(final_response, voice=voice)

    if voice:
        await set_state(SystemState.SPEAKING)
        await speak(final_response, is_smart=False, response_id=response_id)
    await set_state(SystemState.IDLE)

    yield f"data: {json.dumps({'text': final_response, 'model': 'groq', 'done': False})}\n\n"
    yield f"data: {json.dumps({'done': True})}\n\n"
    flags.last_response_time = _time.time()


async def process_command(command_text: str, request_id: str = None, voice: bool = False):
    """
    Shared logic to process a command from either voice or chat.
    Yields SSE-formatted payloads for the frontend stream.
    """
    now = _time.time()
    print(f"[Core] Processing command: {command_text!r} (ID: {request_id})")

    if not command_text.strip():
        await set_state(SystemState.IDLE)
        yield f"data: {json.dumps({'done': True})}\n\n"
        return

    # Phantom filter is for STT echo/hallucinations only — never block typed chat.
    if voice:
        from stt.filter import is_phantom_transcript
        if is_phantom_transcript(command_text, last_assistant=flags.last_assistant_response):
            print(f"[Core] Ignoring phantom/hallucinated input: {command_text!r}")
            await set_state(SystemState.IDLE)
            yield f"data: {json.dumps({'done': True})}\n\n"
            return

    cmd_lower = command_text.strip().lower().rstrip(".!?,")
    if cmd_lower in WAKE_PHRASES:
        print("[Backend] Wake phrase only — treating as greeting.")
        command_text = cmd_lower

    with flags.state_lock:
        is_duplicate = (command_text == flags.last_user_input and now - flags.last_request_time < 3)
        if not command_text or is_duplicate:
            blocked = True
        elif request_id and request_id in flags.processed_ids:
            print(f"[Backend] Request {request_id} already seen. Ignoring.")
            blocked = True
        elif now - flags.last_response_time < 2 and not _is_control_command(command_text):
            print("[Backend] Response guard active. Ignoring.")
            blocked = True
        elif flags.is_processing:
            print("[Backend] Already processing. Ignoring.")
            blocked = True
        else:
            blocked = False

    if blocked:
        await set_state(SystemState.IDLE)
        yield f"data: {json.dumps({'done': True})}\n\n"
        return

    with flags.state_lock:
        flags.is_processing = True
        flags.last_request_time = now
        flags.last_user_input = command_text
        if request_id:
            flags.processed_ids.add(request_id)

    try:
        command_epoch = flags.speak_epoch
        response_id = str(uuid.uuid4())
        await set_state(SystemState.PROCESSING)

        cmd_clean = command_text.lower().strip().rstrip("?!., ")
        if voice and _is_explicit_call_end(cmd_clean):
            # Block phantom "end the call" right after starting music (common STT false positive)
            if (
                flags.last_intent == "play_local_music"
                and now - flags.last_response_time < 10
            ):
                print("[Core] Ignoring suspect call-end right after play music.")
                await set_state(SystemState.IDLE_LISTENING if flags.continuous_voice_mode else SystemState.IDLE)
                yield f"data: {json.dumps({'done': True})}\n\n"
                return
            async for frame in _handle_call_termination(voice=voice, response_id=response_id):
                yield frame
            return

        print(f"[Core] Resolving intent for: {command_text}")
        command_text, resolved_params = resolve_pronouns(command_text)
        from brain.router import route_command
        intent, params = route_command(command_text)
        if resolved_params:
            params = {**(params or {}), **resolved_params}

        if not intent:
            print("[Core] No hardcoded route found. Consulting LLM Brain...")
            context = get_current_context()
            time_context = f"\nCurrent Local Time: {datetime.now().strftime('%A, %B %d, %Y, %I:%M %p')}\n"
            context = time_context + context
            action_json = await asyncio.to_thread(decide_action, command_text, context)
            intent = action_json.get("intent")
            params = action_json.get("parameters", {})
        else:
            print(f"[Core] Hardcoded intent found: {intent}")
            action_json = {"intent": intent, "parameters": params or {}}

        print(f"[Core] Final Intent: {intent}, Params: {params}")

        final_response: str | None = None

        if intent == "greeting":
            tod = _time_of_day()
            final_response = f"Good {tod}, sir. How can I help you today?"

        elif intent == "capabilities":
            final_response = await _groq_generate(
                "The user asked what you can do. List your capabilities concisely in 2-3 sentences. "
                "Do not greet. Start directly with what you can do. "
                "You can: answer questions, open apps, play local garage music, play songs on YouTube Music, "
                "YouTube Search, or Spotify, control music with pause, resume, stop, restart, and music volume "
                "from 0 to 100, plus system volume control, "
                "send WhatsApp messages, "
                "search the web, tell jokes, read news headlines, and run autonomous browser tasks.",
                system=JARVIS_SYSTEM_PROMPT,
            )

        elif intent == "news":
            news_ack = "Checking the latest headlines, sir."
            await _push_chat(news_ack, voice=voice)
            if not voice:
                yield f"data: {json.dumps({'text': news_ack, 'model': 'groq', 'done': False})}\n\n"
            summary = await _fetch_news_summary()
            final_response = summary

        elif intent == "joke":
            import random
            from brain.jokes_data import JOKES
            final_response = random.choice(JOKES)

        elif intent == "time":
            now_dt = datetime.now()
            final_response = (
                f"It is {now_dt.strftime('%I:%M %p')} on "
                f"{now_dt.strftime('%A, %B %d, %Y')}, sir."
            )

        elif intent == "qa":
            final_response = await _groq_generate(
                f"Question (voice transcript — may contain speech-to-text errors, "
                f"interpret phonetically): {command_text}\n"
                "Examples: 'nice in a mind' likely means 'niacinamide'. "
                "Answer the user's intended question in 1 to 3 sentences. "
                "No greeting. Start with the answer immediately.",
                system=QA_SYSTEM_PROMPT,
            )

        elif intent == "intro":
            final_response = await _groq_generate(
                "Introduce yourself as J.A.R.V.I.S. in 2-3 sentences. "
                "Calm, precise, slightly formal. No time-of-day greeting.",
                system=JARVIS_SYSTEM_PROMPT,
            )

        elif intent == "focus_window":
            await manager.broadcast_json({"action": "focus_window"})
            final_response = "Bringing the interface back to focus, sir."

        elif intent == "web_agent":
            from executor.web_agent import run_web_agent_streaming
            task_desc = (params or {}).get("task") or command_text

            ack = f"Understood. Running the autonomous agent on: {task_desc}"
            await _push_chat(ack, voice=voice)
            if not voice:
                yield f"data: {json.dumps({'text': ack, 'model': 'groq', 'done': False})}\n\n"
            if flags.speak_epoch == command_epoch:
                await speak(ack, is_smart=False, response_id=f"{response_id}_ack")
            await set_state(SystemState.PROCESSING)

            last_summary = "Task completed, sir."
            async for sse_chunk in run_web_agent_streaming(
                task=task_desc,
                broadcast_fn=manager.broadcast_json,
                max_steps=15,
                use_vision=True,
            ):
                yield sse_chunk
                try:
                    payload = json.loads(sse_chunk.lstrip("data: ").strip())
                    if payload.get("status") in ("done", "stopped"):
                        last_summary = payload.get("result", last_summary)
                except Exception:
                    pass

            final_response = last_summary
            if flags.speak_epoch == command_epoch:
                asyncio.create_task(_speak_in_background(final_response, True, response_id))
            else:
                await set_state(SystemState.IDLE)
            await manager.broadcast_json({"action": "focus_window"})
            yield f"data: {json.dumps({'text': final_response, 'model': 'groq', 'done': False})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
            flags.last_response_time = _time.time()
            return

        elif intent == "chat" and params and params.get("response"):
            final_response = params["response"]

        elif intent == "chat" or (intent is None and not final_response):
            if _is_greeting_text(command_text):
                tod = _time_of_day()
                final_response = f"Good {tod}, sir. How can I help you today?"
                intent = "greeting"
            else:
                final_response = await _groq_generate(
                    f"{command_text}\n"
                    "Reply naturally in 1-3 sentences. Do not greet unless the user just said hello. "
                    "Never claim you adjusted lights, devices, or the environment unless a tool actually did so.",
                    system=JARVIS_SYSTEM_PROMPT,
                )

        elif intent == "play_local_music":
            add_to_history(command_text)
            final_response = "Playing garage music, sir."

        else:
            success, result = await execute_tool(action_json)

            if success:
                add_to_history(command_text)
                final_response = result if isinstance(result, str) else respond_success(intent, params or {})
                if intent not in ("play_local_music", "music_control", "volume_control"):
                    await manager.broadcast_json({"action": "focus_window"})
            else:
                final_response = result if isinstance(result, str) and result else respond_fail(intent, params or {})

        if final_response:
            if intent != "greeting":
                final_response = _strip_leading_greeting(final_response)
            flags.last_assistant_response = final_response
            if flags.speak_epoch != command_epoch:
                await set_state(SystemState.IDLE)
            else:
                is_smart = intent in ["search_browser", "chat", "read_headlines", "smart_search", "news", "intro", "capabilities", "qa"]
                if intent == "play_local_music":
                    asyncio.create_task(_speak_then_play_music(final_response, response_id))
                else:
                    # Start TTS before pushing text so audio begins while the UI updates.
                    asyncio.create_task(_speak_in_background(final_response, is_smart=is_smart, response_id=response_id))
            await _push_chat(final_response, voice=voice)
        else:
            await set_state(SystemState.IDLE)

        if final_response:
            yield f"data: {json.dumps({'text': final_response, 'model': 'groq', 'done': False})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

        flags.last_response_time = _time.time()
        if intent:
            flags.last_intent = intent

    except Exception as e:
        print(f"[Process Error] {str(e)}")
        await set_state(SystemState.IDLE)
        yield f"data: {json.dumps({'error': str(e), 'done': True})}\n\n"
    finally:
        with flags.state_lock:
            flags.is_processing = False


async def process_command_with_timeout(command_text: str, request_id: str = None, voice: bool = False):
    """Wrapper with hard timeout to prevent state-locks.

    Voice/STT sessions get a longer budget so tools can finish and TTS can speak
    a proper reply instead of cutting off mid-task.
    """
    timeout_s = 90.0 if voice else 45.0
    try:
        async with asyncio.timeout(timeout_s):
            async for item in process_command(command_text, request_id, voice):
                yield item
    except TimeoutError:
        print(f"[Core] Process command timed out after {timeout_s}s! Forcefully resetting state.")
        flags.is_processing = False
        await set_state(SystemState.IDLE)
        yield f"data: {json.dumps({'error': 'Process timed out', 'done': True})}\n\n"
