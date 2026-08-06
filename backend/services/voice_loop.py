"""
voice_loop.py — Event-Driven Voice Command Loop for JARVIS
==========================================================
Continuous voice-call mode:
  listen -> transcribe -> think -> speak -> perform -> listen again
until the user ends the call (mic off or spoken phrase like "end the call").
"""

import asyncio
import traceback
from datetime import datetime

from services.event_bus import event_bus, BusEvent, EventType
from services.runtime_state import flags, SystemState
from services.websocket_manager import manager
from stt.wake import wait_for_wake_word
from stt.stt import listen_stream


_current_state = SystemState.IDLE
_session_lock = asyncio.Lock()


async def set_state(new_state: SystemState):
    global _current_state
    _current_state = new_state
    await manager.broadcast_state(new_state.value)
    print(f"[State] {new_state.name}")


def get_current_state() -> SystemState:
    return _current_state


def _time_of_day() -> str:
    hour = datetime.now().hour
    if hour < 12:
        return "morning"
    elif hour < 17:
        return "afternoon"
    return "evening"


def _ready_to_listen() -> bool:
    return _current_state in (
        SystemState.IDLE,
        SystemState.IDLE_LISTENING,
        SystemState.SPEAKING,
    )


# ── Wake Detector (background task) ─────────────────────────────────────────

_wake_signal = asyncio.Event()


async def _run_wake_detector():
    """Listen for wake word when not in an active voice-call session."""
    from services.shutdown import is_shutting_down

    while not is_shutting_down():
        try:
            if flags.continuous_voice_mode or flags.voice_session_active:
                await asyncio.sleep(0.2)
                continue

            def check_trigger():
                return flags.force_listen_trigger

            detected = await asyncio.to_thread(
                wait_for_wake_word, stop_check=check_trigger
            )

            if is_shutting_down():
                break

            if detected:
                _wake_signal.set()

            await asyncio.sleep(0.05)

        except Exception as e:
            if is_shutting_down():
                break
            print(f"[Wake Detector] Error: {e}")
            await asyncio.sleep(1.0)

    print("[Wake Detector] Stopped.")


# ── Wait for TTS / processing to finish before next listen ───────────────────

async def _wait_until_ready_to_listen():
    """Block until JARVIS finishes speaking or thinking so the mic can open."""
    from tts.pocket_tts import is_speaking

    tts_was_active = False
    for _ in range(120):  # up to ~60s
        if not flags.continuous_voice_mode or flags.stop_listen_trigger:
            return

        speaking = is_speaking()
        if speaking:
            tts_was_active = True

        ready = (
            not flags.is_processing
            and not flags.is_listening
            and not speaking
            and _current_state not in (
                SystemState.PROCESSING, SystemState.SPEAKING,
                SystemState.LISTENING, SystemState.TRANSCRIBING,
            )
        )
        if ready:
            # Brief pause after TTS so the mic does not pick up speaker echo
            if tts_was_active:
                await asyncio.sleep(1.5)
            return
        await asyncio.sleep(0.5)


# ── Listen + Process Cycle ──────────────────────────────────────────────────

async def _listen_and_process() -> str:
    """
    Full listen -> transcribe -> process cycle.
    Returns: "processed", "empty", "blocked", or "terminated".
    """
    from tts.pocket_tts import stop_speech
    stop_speech()

    with flags.state_lock:
        if flags.is_listening:
            return "blocked"
        if not _ready_to_listen():
            print(f"[VoiceLoop] Blocked: State={_current_state.name}")
            return "blocked"
        if flags.stop_listen_trigger or not flags.continuous_voice_mode:
            return "terminated"

    await manager.broadcast_json({"type": "wake_word_detected"})
    await set_state(SystemState.LISTENING)
    with flags.state_lock:
        flags.is_listening = True

    await asyncio.sleep(0.2)

    loop = asyncio.get_event_loop()

    def partial_cb(partial_text: str, countdown: int = None):
        print(f"[Live STT] {partial_text} (countdown: {countdown})")
        asyncio.run_coroutine_threadsafe(set_state(SystemState.TRANSCRIBING), loop)
        asyncio.run_coroutine_threadsafe(
            manager.broadcast_json({
                "type": "partial_transcript",
                "text": partial_text,
                "countdown": countdown,
            }),
            loop,
        )

    def get_text() -> str:
        return listen_stream(partial_cb=partial_cb, stop_event=flags.stop_event)

    from executor.music_services import pause_for_listening, resume_after_listening
    paused_music_for_stt = False
    try:
        paused_music_for_stt = pause_for_listening()
        command_text = await asyncio.to_thread(get_text)
    finally:
        if paused_music_for_stt:
            resume_after_listening()

    import time
    now = time.time()

    with flags.state_lock:
        flags.is_listening = False

        if flags.stop_listen_trigger or not flags.continuous_voice_mode:
            await manager.broadcast_json({"type": "transcript_clear"})
            await set_state(SystemState.IDLE)
            return "terminated"

        if not command_text or flags.is_processing or (now - flags.last_request_time < 1.5):
            if command_text:
                print(f"[VoiceLoop] Debounce skip: {command_text!r}")
            await manager.broadcast_json({"type": "transcript_clear"})
            if flags.continuous_voice_mode:
                await set_state(SystemState.IDLE_LISTENING)
            else:
                await set_state(SystemState.IDLE)
            return "empty"

        flags.last_request_time = now

    from stt.correct import correct_transcript
    from stt.filter import is_phantom_transcript

    command_text = correct_transcript(command_text)
    if is_phantom_transcript(command_text, last_assistant=flags.last_assistant_response):
        print(f"[VoiceLoop] Ignoring phantom STT: {command_text!r}")
        await manager.broadcast_json({"type": "transcript_clear"})
        if flags.continuous_voice_mode:
            await set_state(SystemState.IDLE_LISTENING)
        else:
            await set_state(SystemState.IDLE)
        return "empty"

    if not command_text:
        await manager.broadcast_json({"type": "transcript_clear"})
        if flags.continuous_voice_mode:
            await set_state(SystemState.IDLE_LISTENING)
        else:
            await set_state(SystemState.IDLE)
        return "empty"

    print(f"[USER]: {command_text}")
    await set_state(SystemState.PROCESSING)
    await manager.broadcast_json({"type": "user_message", "text": command_text})

    from services.command_processor import process_command_with_timeout
    async for _ in process_command_with_timeout(command_text, voice=True):
        pass

    if flags.stop_listen_trigger or not flags.continuous_voice_mode:
        await set_state(SystemState.IDLE)
        return "terminated"

    await set_state(SystemState.IDLE_LISTENING)
    print("-" * 30)
    return "processed"


# ── Continuous voice-call session ────────────────────────────────────────────

async def _run_continuous_session():
    """
    Persistent call loop: listen -> transcribe -> think -> speak -> act -> listen.
    Runs until continuous_voice_mode is cleared or user ends the call.
    """
    if flags.voice_session_active:
        print("[VoiceLoop] Voice session already active — skipping duplicate start.")
        return

    async with _session_lock:
        if flags.voice_session_active:
            return

        flags.voice_session_active = True
        flags.continuous_voice_mode = True
        flags.stop_listen_trigger = False
        flags.force_listen_trigger = False
        flags.stop_event.clear()
        print("[VoiceLoop] Voice-call session started — continuous listening ON.")

        try:
            from services.shutdown import is_shutting_down

            while (
                flags.continuous_voice_mode
                and not flags.stop_listen_trigger
                and not is_shutting_down()
            ):
                await _wait_until_ready_to_listen()
                if not flags.continuous_voice_mode or flags.stop_listen_trigger:
                    break

                result = await _listen_and_process()
                if result == "terminated":
                    break
                if result == "blocked":
                    await asyncio.sleep(0.5)
                    continue

                await asyncio.sleep(0.3)

        finally:
            flags.voice_session_active = False
            flags.is_listening = False
            if flags.stop_listen_trigger or not flags.continuous_voice_mode:
                await set_state(SystemState.IDLE)
                print("[VoiceLoop] Voice-call session ended.")
            else:
                await set_state(SystemState.IDLE_LISTENING)


# ── Stop Handler ─────────────────────────────────────────────────────────────

async def _handle_stop():
    """Handle stop request: kill TTS, reset all flags, go idle."""
    from tts.hybrid_tts import force_stop_all_tts
    force_stop_all_tts()
    flags.speak_epoch += 1
    flags.force_listen_trigger = False
    flags.stop_listen_trigger = True
    flags.continuous_voice_mode = False
    flags.voice_session_active = False
    flags.stop_event.set()
    await set_state(SystemState.IDLE)
    print("[VoiceLoop] Stop triggered.")


# ── Text Command Handler ─────────────────────────────────────────────────────

async def _handle_text_command(text: str, *, voice: bool = False):
    if not text.strip():
        return

    await set_state(SystemState.PROCESSING)
    await manager.broadcast_json({"type": "user_message", "text": text})

    from services.command_processor import process_command_with_timeout
    async for _ in process_command_with_timeout(text, voice=voice):
        pass

    if flags.continuous_voice_mode and not flags.stop_listen_trigger:
        await set_state(SystemState.IDLE_LISTENING)
    else:
        await set_state(SystemState.IDLE)


# ── Main Loop ────────────────────────────────────────────────────────────────

async def voice_command_loop():
    print("\n[VoiceLoop] Voice Command Loop started. Standby...")
    await asyncio.sleep(2)
    print("[VoiceLoop] Systems online. Say 'Hey Jarvis' or click Voice Mode.")

    tod = _time_of_day()
    await manager.broadcast_json({"type": "system_ready"})

    asyncio.create_task(_run_wake_detector())

    from services.shutdown import is_shutting_down, get_shutdown_event

    try:
        while not is_shutting_down():
            wake_task = asyncio.create_task(_wake_signal.wait())
            bus_task = asyncio.create_task(event_bus.next_event())
            shutdown_task = asyncio.create_task(get_shutdown_event().wait())

            done, pending = await asyncio.wait(
                [wake_task, bus_task, shutdown_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            if shutdown_task in done:
                print("[VoiceLoop] Shutdown signal received — exiting.")
                break

            if wake_task in done:
                _wake_signal.clear()
                flags.force_listen_trigger = False
                flags.stop_event.clear()
                print("[VoiceLoop] Wake word detected!")
                await _listen_and_process()

            elif bus_task in done:
                event = bus_task.result()

                if event.event_type == EventType.WAKE:
                    print("[VoiceLoop] Voice-call mode triggered (mic / UI).")
                    asyncio.create_task(_run_continuous_session())

                elif event.event_type == EventType.STOP:
                    await _handle_stop()

                elif event.event_type == EventType.COMMAND:
                    text = event.payload.get("text", "")
                    voice = event.payload.get("voice", False)
                    if text:
                        if voice and flags.continuous_voice_mode:
                            await _handle_text_command(text, voice=True)
                        else:
                            flags.continuous_voice_mode = False
                            await _handle_text_command(text, voice=False)

                elif event.event_type == EventType.MUTE:
                    from brain.settings import update_settings
                    muted = event.payload.get("muted", False)
                    update_settings({"muted": muted})
                    print(f"[VoiceLoop] Mute: {muted}")

    except Exception as e:
        print(f"[VoiceLoop Error] {e}")
        traceback.print_exc()
        await set_state(SystemState.IDLE)
    finally:
        print("[VoiceLoop] Stopped.")