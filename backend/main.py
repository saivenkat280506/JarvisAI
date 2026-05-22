"""
main.py — JARVIS Unified Core
==============================
Integrates LLM decision layer, memory, and automation.
"""

import asyncio
import json
from datetime import datetime
import os
import sys
import traceback
import tempfile
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager
from dotenv import load_dotenv

# Add backend and root to path
sys.path.append(os.path.join(os.path.dirname(__file__), '.'))
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from tts.hybrid_tts import speak_hybrid as speak
from executor.tool_executor import execute_tool
from stt.wake import wait_for_wake_word
from stt.stt import listen_stream
from brain.llm_brain import decide_action
from brain.context_manager import get_current_context, resolve_pronouns
from brain.memory import add_to_history, save_memory
from brain.responses import get_response
from brain.personality import respond_start, respond_success, respond_fail, respond_background, respond_cancel, respond_processing
from config import settings

# ── Jarvis Personality System Prompt ────────────────────────────────────────
JARVIS_SYSTEM_PROMPT = """
You are J.A.R.V.I.S. — Just A Rather Very Intelligent System.
Personality: calm, precise, slightly formal, dry wit, voice-friendly.
Rules:
- Reply in 1-3 short sentences max.
- Never use markdown, bullet points, or symbols.
- Sound like Paul Bettany's Jarvis, not a chatbot.
- Address user as "sir" occasionally, not every sentence.
"""

from dotenv import load_dotenv

if getattr(sys, 'frozen', False):
    env_path = os.path.join(os.path.dirname(sys.executable), '.env')
else:
    env_path = os.path.join(os.path.dirname(__file__), '.env')
load_dotenv(env_path)

# ── Global Guards ────────────────────────────────────────────────────────────
_is_processing = False
_processed_ids = set()
_last_request_time = 0
_last_user_input = ""
_last_response_time = 0
_is_listening = False
_mic_muted = False
_force_listen_trigger = False
_stop_listen_trigger = False
_continuous_voice_mode = False
import threading
_stop_event = threading.Event()

# ── 1. Hard State Control ───────────────────────────────────────────────────
import uuid
from enum import Enum
class SystemState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "thinking"
    SPEAKING = "talking"
    TRANSCRIBING = "transcribing"

_current_state = SystemState.IDLE

async def set_state(new_state: SystemState):
    global _current_state
    if _current_state == new_state:
        return
    _current_state = new_state
    await manager.broadcast_state(new_state.value)
    print(f"[State] {new_state.name}")

# ── Log Only Once Helper ───────────────────────────────────────────────────
_last_error = ""
def log_once(error_msg: str):
    global _last_error
    if error_msg != _last_error:
        print(f"[Error] {error_msg}")
        _last_error = error_msg

# ── Core Logic Helper ────────────────────────────────────────────────────────────
async def _groq_generate(prompt: str, system: str = None) -> str:
    """
    Lightweight async Groq call that returns a plain-text string.
    Used for joke / intro / news-summary tasks.
    """
    import httpx, os
    from datetime import datetime
    groq_key = settings.GROQ_API_KEY
    if not groq_key:
        return "I seem to have misplaced my API key, sir."
        
    now_dt = datetime.now()
    time_str = now_dt.strftime("%A, %B %d, %Y, %I:%M %p")
    time_context = f"\n\nCurrent Context: The current local time is {time_str}. Make sure your greetings (Good morning/afternoon/evening) match this exactly."
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
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        print(f"[Groq] Error: {exc}")
        return "I ran into a small issue there, sir."


async def _fetch_news_summary() -> str:
    """
    Fetches top headlines then passes them through Groq for a
    single-paragraph human-tone summary.  Never exposes raw API data.
    """
    import httpx, os, json as _json
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
        # Fallback: lightweight scrape via GNews RSS (no key required)
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get("https://news.google.com/rss", follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
                import xml.etree.ElementTree as ET
                root = ET.fromstring(r.text)
                headlines = [item.find('title').text for item in root.findall('.//item')][:6]
        except Exception as exc:
            print(f"[News] RSS fallback error: {exc}")

    if not headlines:
        return "I wasn't able to pull the latest headlines, sir. Network may be restricted."

    bullet_list = "\n".join(f"- {h}" for h in headlines[:3])
    prompt = (
        f"Here are today's top trending headlines:\n{bullet_list}\n\n"
        "The user asked for the latest news headlines summary. "
        "Provide a very brief, elegant, and snappy summary of the top 3 headlines. "
        "State only the main points in a natural, spoken conversational way (e.g., 'Good evening, sir. First, [headline 1]. Second, [headline 2]. Lastly, [headline 3].'). "
        "Do NOT elaborate on detailed backstories or add extra sentences for each story. "
        "Keep the entire spoken response short, clean, and under 12-15 seconds total. "
        "Do not use markdown, lists, or bullets."
    )
    return await _groq_generate(
        prompt,
        system="You are J.A.R.V.I.S. Provide a sophisticated news briefing. Speak clearly and smoothly without markdown symbols.",
    )


async def _speak_in_background(text: str, is_smart: bool, response_id: str):
    try:
        await speak(text, is_smart=is_smart, response_id=response_id)
    finally:
        await set_state(SystemState.IDLE)


async def process_command(command_text: str, request_id: str = None):
    """
    Shared logic to process a command from either voice or chat.
    Yields SSE-formatted payloads for the frontend stream.

    SINGLE SOURCE OF TRUTH: every final_response is sent to
    both the chat stream AND TTS — no separate generation.
    """
    global _is_processing, _last_user_input, _last_request_time, _last_response_time
    import time, json
    now = time.time()

    print(f"[Core] Processing command: {command_text!r} (ID: {request_id})")
    
    # ── Guard: process lock + dedup ──────────────────────────────────────────
    # Discard empty strings (ambient noise or partials)
    if not command_text.strip():
        return

    # Discard if the command is literally just the wake phrase (self-echo)
    _WAKE_PHRASES = {"jarvis", "hey jarvis", "wake up jarvis", "wake jarvis", "jarvis listen"}
    if command_text.strip().lower() in _WAKE_PHRASES:
        print(f"[Backend] Command is the wake phrase itself. Ignoring.")
        return

    is_duplicate = (command_text == _last_user_input and now - _last_request_time < 3)
    if not command_text or is_duplicate:
        return
    if request_id and request_id in _processed_ids:
        print(f"[Backend] Request {request_id} already seen. Ignoring.")
        return
    if now - _last_response_time < 2:
        print("[Backend] Response guard active. Ignoring.")
        return

    try:
        _is_processing = True
        _last_request_time = now
        _last_user_input = command_text
        if request_id:
            _processed_ids.add(request_id)

        response_id = str(uuid.uuid4())
        await set_state(SystemState.PROCESSING)

        print(f"[Core] Resolving intent for: {command_text}")
        command_text, resolved_params = resolve_pronouns(command_text)
        from brain.router import route_command
        intent, params = route_command(command_text)
        if resolved_params:
            params = {**(params or {}), **resolved_params}

        if not intent:
            print("[Core] No hardcoded route found. Consulting LLM Brain...")
            context = get_current_context()
            from datetime import datetime
            time_context = f"\nCurrent Local Time: {datetime.now().strftime('%A, %B %d, %Y, %I:%M %p')}\n"
            context = time_context + context
            action_json = await asyncio.to_thread(decide_action, command_text, context)
            intent = action_json.get("intent")
            params  = action_json.get("parameters", {})
        else:
            print(f"[Core] Hardcoded intent found: {intent}")
            action_json = {"intent": intent, "parameters": params or {}}
        
        print(f"[Core] Final Intent: {intent}, Params: {params}")

        # ── Native intent handlers (no execute_tool round-trip) ──────────────
        final_response: str | None = None

        if intent == "greeting":
            # Time-aware greeting every time user says hi/hello
            tod = _time_of_day()
            final_response = f"Good {tod}, sir. How can I help you today?"

        elif intent == "capabilities":
            final_response = await _groq_generate(
                "The user asked what you can do. List your capabilities concisely in 2-3 sentences. "
                "You can: answer questions, open apps, play music on YouTube, send WhatsApp messages, "
                "search the web, tell jokes, read news headlines, and run autonomous browser tasks.",
                system=JARVIS_SYSTEM_PROMPT,
            )

        elif intent == "news":
            # Acknowledge immediately
            ack = "Checking the latest headlines, sir."
            await manager.broadcast_chat(ack)
            await speak(ack, is_smart=False, response_id=f"{response_id}_ack")
            summary = await _fetch_news_summary()
            final_response = summary

        elif intent == "joke":
            import random
            from brain.jokes_data import JOKES
            final_response = random.choice(JOKES)

        elif intent == "qa":
            final_response = await _groq_generate(
                f"The user asked: {command_text}\nAnswer this intelligently, clearly, and concisely in 1 to 3 sentences max.",
                system=JARVIS_SYSTEM_PROMPT,
            )

        elif intent == "intro":
            final_response = await _groq_generate(
                "Introduce yourself as J.A.R.V.I.S. in 2-3 sentences. "
                "Calm, precise, slightly formal.",
                system=JARVIS_SYSTEM_PROMPT,
            )

        elif intent == "focus_window":
            await manager.broadcast_json({"action": "focus_window"})
            final_response = "Bringing the interface back to focus, sir."

        elif intent == "web_agent":
            # ── Autonomous agent: stream every step via WebSocket ──────────────
            from executor.web_agent import run_web_agent_streaming
            task_desc = (params or {}).get("task") or command_text

            ack = f"Understood. Running the autonomous agent on: {task_desc}"
            await manager.broadcast_chat(ack)
            await speak(ack, is_smart=False, response_id=f"{response_id}_ack")
            await set_state(SystemState.PROCESSING)

            # Run the agent; each step is broadcast via WebSocket automatically
            last_summary = "Task completed, sir."
            async for sse_chunk in run_web_agent_streaming(
                task=task_desc,
                broadcast_fn=manager.broadcast_json,
                max_steps=15,
                use_vision=True,
            ):
                # Also yield through the SSE channel so the UI stream stays alive
                yield sse_chunk
                # Extract summary from done/stopped step
                try:
                    payload = json.loads(sse_chunk.lstrip("data: ").strip())
                    if payload.get("status") in ("done", "stopped"):
                        last_summary = payload.get("result", last_summary)
                except Exception:
                    pass

            final_response = last_summary
            await set_state(SystemState.SPEAKING)
            asyncio.create_task(_speak_in_background(final_response, True, response_id))
            await manager.broadcast_json({"action": "focus_window"})
            # Skip the standard response yield below — already yielded above
            yield f"data: {json.dumps({'text': final_response, 'model': 'groq', 'done': False})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
            _last_response_time = time.time()
            return

        elif intent == "chat" and params and params.get("response"):
            # Direct hardcoded chat responses from router — still show then speak
            final_response = params["response"]

        elif intent == "chat" or (intent is None and not final_response):
            # Any unmatched vague chat → Groq for a real answer
            final_response = await _groq_generate(command_text, JARVIS_SYSTEM_PROMPT)

        else:
            # ── Tool execution path ──────────────────────────────────────────
            success, result = await execute_tool(action_json)
            await set_state(SystemState.SPEAKING)

            if success:
                add_to_history(command_text)
                final_response = result if isinstance(result, str) else respond_success(intent, params or {})
                # Emit window focus after external action
                await manager.broadcast_json({"action": "focus_window"})
            else:
                # ── Smart fallback: Groq answers EVERYTHING ───────────────────
                final_response = await _groq_generate(command_text, JARVIS_SYSTEM_PROMPT)

        # ── SINGLE SOURCE OF TRUTH: show first, then speak ──────────────────
        if final_response:
            await set_state(SystemState.SPEAKING)
            # 1. Push text to chat UI immediately so user sees it
            await manager.broadcast_chat(final_response)
            # 2. Start TTS in background (runs while user is already reading the text)
            is_smart = intent in ["search_browser", "chat", "read_headlines", "smart_search", "news", "intro", "greeting", "capabilities", "qa"]
            asyncio.create_task(_speak_in_background(final_response, is_smart=is_smart, response_id=response_id))
        else:
            await set_state(SystemState.IDLE)

        # Stream the final text to the SSE caller (chat endpoint)
        if final_response:
            yield f"data: {json.dumps({'text': final_response, 'model': 'groq', 'done': False})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

        _last_response_time = time.time()

    except Exception as e:
        log_once(f"Process Error: {str(e)}")
        await set_state(SystemState.IDLE)
        err_msg = respond_fail("general")
        # Only yield the error via SSE — the frontend shows it.
        # Do NOT also broadcast_chat or we get a duplicate message.
        yield f"data: {json.dumps({'error': str(e), 'done': True})}\n\n"
    finally:
        _is_processing = False

async def process_command_with_timeout(command_text: str, request_id: str = None):
    """
    Wrapper for process_command that enforces a hard timeout to prevent state-locks.
    """
    try:
        async with asyncio.timeout(30.0):
            async for item in process_command(command_text, request_id):
                yield item
    except TimeoutError:
        print("[Core] Process command timed out! Forcefully resetting state.")
        global _is_processing
        _is_processing = False
        await set_state(SystemState.IDLE)
        yield f"data: {json.dumps({'error': 'Process timed out', 'done': True})}\n\n"

# ── WebSocket Connection Manager ────────────────────────────────────────────────
# ── Connection Manager and Endpoints will follow after app definition ──

# ── Core Logic Loop ─────────────────────────────────────────────────────────────
def _time_of_day() -> str:
    hour = datetime.now().hour
    if hour < 12:
        return "morning"
    elif hour < 17:
        return "afternoon"
    return "evening"


async def voice_command_loop():
    print("\n[Core] Voice Command Loop started. Standby...")

    # ── Startup system-ready notification (no greeting — wake word does that) ──
    await asyncio.sleep(2)
    print("[Core] Systems online. Listening for wake phrase...")
    
    # ── Time-aware startup greeting broadcast to frontend ──────────────────────
    tod = _time_of_day()
    startup_greeting = f"Good {tod}, sir. How can I help you today?"
    await manager.broadcast_chat(startup_greeting)
    await manager.broadcast_json({"type": "system_ready"})

    try:
        while True:
            global _force_listen_trigger, _continuous_voice_mode
            
            def check_trigger():
                return _force_listen_trigger or _continuous_voice_mode

            # Run wake detection with UI-override check
            _barge_in_fired = False  # Debounce guard for barge-in
            def on_barge_in():
                nonlocal _barge_in_fired
                if _current_state == SystemState.SPEAKING and not _barge_in_fired:
                    _barge_in_fired = True
                    print("[Core] Barge-in VAD trigger! Stopping speech...")
                    import threading
                    from tts.pocket_tts import stop_speech
                    threading.Thread(target=stop_speech, daemon=True).start()

            detected = await asyncio.to_thread(wait_for_wake_word, stop_check=check_trigger, barge_in_callback=on_barge_in)
            
            if detected:
                from tts.pocket_tts import stop_speech
                stop_speech()

            _force_listen_trigger = False # Reset trigger
            _stop_listen_trigger = False
            _stop_event.clear()
            print("[Core] Listening triggered!")

            # Prevent re-entry
            global _is_listening, _current_state
            if _is_listening:
                continue
            if _current_state != SystemState.IDLE and _current_state != SystemState.SPEAKING:
                print(f"[Core] Blocked: State={_current_state.name}")
                continue

            # ── 2. Notify frontend immediately so UI shows 'listening' state ──
            await manager.broadcast_json({"type": "wake_word_detected"})
            await set_state(SystemState.LISTENING)
            _is_listening = True

            # (No wake greeting — jumping straight to listening per user request)

            # ── 3. Wait for TTS echo to fade before opening mic ────────────
            await asyncio.sleep(0.2)

            # ── 5. Capture voice command + stream partials to frontend ─────
            loop = asyncio.get_event_loop()

            def partial_cb(partial_text: str, countdown: int = None):
                """Called from sounddevice thread — push to async loop safely."""
                print(f"[Live STT] {partial_text} (countdown: {countdown})")
                asyncio.run_coroutine_threadsafe(set_state(SystemState.TRANSCRIBING), loop)
                asyncio.run_coroutine_threadsafe(
                    manager.broadcast_json({"type": "partial_transcript", "text": partial_text, "countdown": countdown}),
                    loop,
                )

            def get_text() -> str:
                return listen_stream(partial_cb=partial_cb, stop_event=_stop_event)

            command_text = await asyncio.to_thread(get_text)

            import time as _time
            now = _time.time()
            global _is_processing, _last_request_time

            if not command_text or _is_processing or (now - _last_request_time < 1.5):
                if command_text:
                    print(f"[Core] Debounce skip: {command_text!r}")
                _is_listening = False
                await manager.broadcast_json({"type": "transcript_clear"})
                await manager.broadcast_state("idle")
                continue

            _last_request_time = now
            print(f"[USER]: {command_text}")

            # Instantly set state to PROCESSING so UI updates immediately
            await set_state(SystemState.PROCESSING)

            # ── 5. Push user message to chat immediately ───────────────────────
            #    This makes the chat feel instant — the bubble appears before
            #    the LLM responds.
            await manager.broadcast_json({"type": "user_message", "text": command_text})

            # ── 6. Process and respond ────────────────────────────────────────
            async for _ in process_command_with_timeout(command_text):
                pass

            _is_listening = False
            
            # Since we just processed a voice command, loop back into voice mode automatically
            if not _stop_listen_trigger:
                _continuous_voice_mode = True
                
            await manager.broadcast_state("idle")
            print("-" * 30)

    except Exception as e:
        print(f"[Core Error] {e}")
        traceback.print_exc()
        await manager.broadcast_state("idle")

# ── FastAPI Setup ──────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the Agent Loop (Background monitoring)
    from executor.agent_loop import agent_loop
    asyncio.create_task(agent_loop.run())
    
    # Start the Voice Command Loop
    asyncio.create_task(voice_command_loop())
    yield
    print("[Server] Shutting down.")


app = FastAPI(title="Jarvis Backend", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_state(self, state: str):
        for ws in self.active_connections:
            try:
                await ws.send_json({"state": state})
            except:
                pass

    async def broadcast_chat(self, text: str, role: str = "assistant"):
        for ws in self.active_connections:
            try:
                await ws.send_json({"type": "chat", "text": text, "role": role})
            except:
                pass

    async def broadcast_json(self, payload: dict):
        """Broadcast arbitrary JSON payload to all connected clients."""
        for ws in self.active_connections:
            try:
                await ws.send_json(payload)
            except:
                pass

manager = ConnectionManager()

@app.get("/settings")
async def get_settings_endpoint():
    from brain.settings import get_settings as gs
    return gs()

@app.post("/settings")
async def save_settings_endpoint(payload: dict):
    from brain.settings import update_settings
    updated = update_settings(payload)
    return updated

@app.post("/toggle-mute")
async def toggle_mute_endpoint():
    from brain.settings import toggle_mute as tm
    muted = tm()
    return {"muted": muted}

@app.get("/health")
async def health():
    return {"status": "online"}

@app.post("/chat")
async def chat_endpoint(request: dict):
    global _continuous_voice_mode
    _continuous_voice_mode = False  # Text chat interrupts voice mode

    from tts.pocket_tts import stop_speech
    stop_speech()

    text = request.get("text", "")
    request_id = request.get("id")
    return StreamingResponse(process_command_with_timeout(text, request_id), media_type="text/event-stream")

@app.post("/listen-trigger")
async def listen_trigger():
    global _force_listen_trigger
    _force_listen_trigger = True
    return {"status": "triggered"}

@app.post("/stop-trigger")
async def stop_trigger():
    """Forces the voice loop to stop immediately."""
    global _force_listen_trigger, _stop_listen_trigger, _continuous_voice_mode
    _force_listen_trigger = False
    _stop_listen_trigger = True
    _continuous_voice_mode = False
    
    from tts.pocket_tts import stop_speech
    stop_speech()
    
    _stop_event.set()
    print("[Backend] Stop trigger received.")
    await set_state(SystemState.IDLE)
    return {"status": "stopping"}

from fastapi import Form
@app.post("/voice")
async def voice_endpoint(audio: UploadFile = File(...), id: str = Form(None)):
    global _is_listening
    if _is_listening:
        print("[Voice] Already listening. Blocking upload.")
        return StreamingResponse(
            iter([f'data: {{"error": "Already listening", "done": true}}\n\n']),
            media_type="text/event-stream",
        )

    _is_listening = True
    text = ""
    try:
        request_id = id
        # 1. Save temporary audio file
        temp_path = os.path.join(tempfile.gettempdir(), f"temp_{audio.filename}")
        with open(temp_path, "wb") as f:
            f.write(await audio.read())

        # 2. Transcribe with faster-whisper; emit each segment as a live
        #    partial-transcript WebSocket event so the UI updates in real-time.
        try:
            from faster_whisper import WhisperModel
            if not hasattr(voice_endpoint, "model"):
                voice_endpoint.model = WhisperModel("base.en", device="cpu", compute_type="int8")

            segments, _ = voice_endpoint.model.transcribe(temp_path, beam_size=2)
            partial_acc = ""
            for seg in segments:
                partial_acc += seg.text
                # Push live partial transcript to UI
                await manager.broadcast_json({
                    "type": "transcript",
                    "text": partial_acc.strip(),
                })
            text = partial_acc.strip()

            # Signal final transcript
            await manager.broadcast_json({
                "type": "user_message",
                "text": text,
            })
        except Exception as e:
            print(f"[Voice] Local transcription failed: {e}. API disabled.")
            text = ""
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    finally:
        _is_listening = False

    if not text:
        import json
        payload_err = json.dumps({"error": "Could not transcribe audio", "done": True})
        return StreamingResponse(iter([f"data: {payload_err}\n\n"]), media_type="text/event-stream")

    # 3. Process command (single source of truth pipeline)
    return StreamingResponse(process_command(text, request_id), media_type="text/event-stream")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ── Agent endpoints ────────────────────────────────────────────────────────────

@app.post("/agent/run")
async def agent_run_endpoint(request: dict):
    """
    SSE endpoint to run the autonomous web/OS agent.
    Streams agent_step events as they happen.

    Body: { "task": "open google and search for python" }
    """
    task = request.get("task", "").strip()
    if not task:
        return {"error": "task is required"}

    from executor.web_agent import run_web_agent_streaming

    async def _stream():
        async for chunk in run_web_agent_streaming(
            task=task,
            broadcast_fn=manager.broadcast_json,
            max_steps=15,
            use_vision=True,
        ):
            yield chunk

    return StreamingResponse(_stream(), media_type="text/event-stream")


@app.post("/agent/stop")
async def agent_stop_endpoint():
    """Signal the running autonomous agent to stop after the current step."""
    from executor.web_agent import request_stop
    request_stop()
    await manager.broadcast_json({"type": "agent_step", "step": 0, "action": "STOPPED", "result": "Stop requested by user.", "status": "stopped"})
    return {"status": "stop_requested"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
