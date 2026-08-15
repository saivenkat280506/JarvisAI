"""
routes.py — HTTP/WS Endpoints for JARVIS
=========================================
Clean separation of API endpoints from core logic.
"""

import os
import json
import subprocess
import io
import wave
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import StreamingResponse

from services.event_bus import event_bus, BusEvent, EventType
from services.runtime_state import flags, SystemState
from services.voice_loop import set_state, get_current_state
from services.websocket_manager import manager
from services.command_processor import process_command, process_command_with_timeout

router = APIRouter()


def _decode_upload_to_pcm(audio_bytes: bytes) -> bytes:
    """Decode uploaded audio into 16 kHz mono signed 16-bit PCM for STT."""
    if not audio_bytes:
        return b""

    # Browser recordings are commonly PCM WAV already. Avoid spawning ffmpeg
    # for this hot path; only resample/convert when the input needs it.
    if audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE":
        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
                channels = wav_file.getnchannels()
                width = wav_file.getsampwidth()
                rate = wav_file.getframerate()
                frames = wav_file.readframes(wav_file.getnframes())
            if channels == 1 and width == 2 and rate == 16000:
                return frames
        except wave.Error:
            pass

    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ac",
            "1",
            "-ar",
            "16000",
            "pipe:1",
        ],
        input=audio_bytes,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Audio decode failed: {err}")
    return result.stdout


# ── Health ───────────────────────────────────────────────────────────────────
@router.get("/health")
async def health():
    return {"status": "online"}


# ── Settings ─────────────────────────────────────────────────────────────────
@router.get("/settings")
async def get_settings_endpoint():
    from brain.settings import get_settings as gs
    return gs()


@router.post("/settings")
async def save_settings_endpoint(payload: dict):
    from brain.settings import update_settings
    return update_settings(payload)


@router.post("/toggle-mute")
async def toggle_mute_endpoint():
    from brain.settings import toggle_mute as tm
    muted = tm()
    return {"muted": muted}


# ── Chat (SSE) ──────────────────────────────────────────────────────────────
@router.post("/chat")
async def chat_endpoint(request: dict):
    from tts.pocket_tts import stop_speech
    stop_speech()
    flags.prepare_for_new_command()

    text = request.get("text", "")
    request_id = request.get("id")
    return StreamingResponse(
        process_command_with_timeout(text, request_id),
        media_type="text/event-stream",
    )


# ── Voice Triggers ──────────────────────────────────────────────────────────
@router.post("/listen-trigger")
async def listen_trigger():
    flags.continuous_voice_mode = True
    flags.stop_listen_trigger = False
    flags.force_listen_trigger = True
    flags.stop_event.clear()
    await event_bus.emit(BusEvent(EventType.WAKE))
    return {"status": "triggered", "continuous_voice_mode": True}


@router.post("/stop-trigger")
async def stop_trigger():
    flags.force_listen_trigger = False
    flags.stop_listen_trigger = True
    flags.continuous_voice_mode = False
    flags.voice_session_active = False
    flags.speak_epoch += 1

    from tts.hybrid_tts import force_stop_all_tts
    force_stop_all_tts()

    flags.stop_event.set()
    print("[Backend] Stop trigger received.")
    await set_state(SystemState.IDLE)
    await event_bus.emit(BusEvent(EventType.STOP))
    return {"status": "stopping"}


# ── File Upload STT ─────────────────────────────────────────────────────────
@router.post("/voice")
async def voice_endpoint(audio: UploadFile = File(...), id: str = Form(None)):
    with flags.state_lock:
        if flags.is_listening:
            print("[Voice] Already listening. Blocking upload.")
            return StreamingResponse(
                iter([f'data: {{"error": "Already listening", "done": true}}\n\n']),
                media_type="text/event-stream",
            )

    with flags.state_lock:
        flags.is_listening = True

    text = ""
    try:
        request_id = id
        upload_bytes = await audio.read()

        try:
            from stt.stt import transcribe_audio
            pcm_data = _decode_upload_to_pcm(upload_bytes)
            text = transcribe_audio(pcm_data)

            if text:
                await manager.broadcast_json({"type": "user_message", "text": text})
        except Exception as e:
            print(f"[Voice] Transcription failed: {e}")
            text = ""
    finally:
        with flags.state_lock:
            flags.is_listening = False

    if not text:
        payload_err = json.dumps({"error": "Could not transcribe audio", "done": True})
        return StreamingResponse(iter([f"data: {payload_err}\n\n"]), media_type="text/event-stream")

    return StreamingResponse(process_command(text, request_id), media_type="text/event-stream")


# ── WebSocket ────────────────────────────────────────────────────────────────
@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            event_type = data.get("event")

            if event_type == "command":
                text = data.get("text", "")
                voice = data.get("voice", False)
                await event_bus.emit(BusEvent(EventType.COMMAND, {"text": text, "voice": voice}))

            elif event_type == "wake":
                flags.continuous_voice_mode = True
                flags.stop_listen_trigger = False
                flags.force_listen_trigger = True
                flags.stop_event.clear()
                await event_bus.emit(BusEvent(EventType.WAKE))

            elif event_type == "stop":
                flags.force_listen_trigger = False
                flags.stop_listen_trigger = True
                flags.continuous_voice_mode = False
                flags.voice_session_active = False
                flags.speak_epoch += 1
                try:
                    from tts.hybrid_tts import force_stop_all_tts
                    force_stop_all_tts()
                except Exception as exc:
                    print(f"[WS Stop] Error stopping TTS: {exc}")
                flags.stop_event.set()
                await set_state(SystemState.IDLE)
                await event_bus.emit(BusEvent(EventType.STOP))

            elif event_type == "mute":
                muted = data.get("muted", False)
                await event_bus.emit(BusEvent(EventType.MUTE, {"muted": muted}))

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    finally:
        manager.disconnect(websocket)


# ── Graceful Shutdown (app close) ───────────────────────────────────────────
@router.post("/shutdown")
async def shutdown_endpoint():
    """Terminate all JARVIS voice, TTS, music, agents, and background tasks."""
    from services.shutdown import shutdown_all_services
    result = await shutdown_all_services(reason="api_shutdown")
    return result


# ── Full Reset (refresh chat) ───────────────────────────────────────────────
@router.post("/reset")
async def reset_endpoint():
    """Terminate all active work: voice session, TTS, agents, tasks, memory."""
    print("[Backend] Reset requested — terminating all active tasks")

    try:
        from tts.hybrid_tts import force_stop_all_tts
        force_stop_all_tts()
    except Exception as exc:
        print(f"[Reset] Error stopping speech: {exc}")

    flags.reset_processing_state()
    await set_state(SystemState.IDLE)
    await event_bus.emit(BusEvent(EventType.STOP))

    try:
        from executor.web_agent import request_stop, clear_stop
        request_stop()
        clear_stop()
    except Exception as exc:
        print(f"[Reset] Error stopping web agent: {exc}")

    try:
        from executor.music_services import stop_local_music
        stop_local_music()
    except Exception as exc:
        print(f"[Reset] Error stopping music: {exc}")

    try:
        from executor.agent_loop import agent_loop
        agent_loop.retry_queue.clear()
    except Exception as exc:
        print(f"[Reset] Error clearing agent retry queue: {exc}")

    task_count = 0
    try:
        from executor.task_manager import task_manager
        for tid in list(task_manager.active_tasks.keys()):
            if task_manager.cancel_task(tid):
                task_count += 1
    except Exception as exc:
        print(f"[Reset] Error cancelling background tasks: {exc}")

    try:
        from brain.memory import MEMORY_FILE, _lock
        mem = {"history": [], "last_contact": None, "last_song": None}
        with _lock:
            with open(MEMORY_FILE, "w", encoding="utf-8") as handle:
                json.dump(mem, handle)
    except Exception as exc:
        print(f"[Reset] Error clearing memory: {exc}")

    try:
        for exe in ("WhatsApp.exe", "WhatsApp.Root.exe"):
            subprocess.run(
                ["taskkill", "/f", "/im", exe],
                capture_output=True,
                text=True,
                check=False,
            )
    except Exception as exc:
        print(f"[Reset] WhatsApp taskkill error: {exc}")

    await manager.broadcast_json({"type": "reset_complete", "state": "idle"})
    print(f"[Backend] Reset complete — stopped {task_count} background tasks")
    return {"status": "ok", "cancelled_tasks": task_count}


# ── Agent Endpoints ──────────────────────────────────────────────────────────
@router.post("/agent/run")
async def agent_run_endpoint(request: dict):
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


@router.post("/agent/stop")
async def agent_stop_endpoint():
    from executor.web_agent import request_stop
    request_stop()
    await manager.broadcast_json({
        "type": "agent_step",
        "step": 0,
        "action": "STOPPED",
        "result": "Stop requested by user.",
        "status": "stopped",
    })
    return {"status": "stop_requested"}
