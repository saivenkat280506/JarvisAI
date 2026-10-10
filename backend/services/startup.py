"""
startup.py — Background Service Launcher for JARVIS
====================================================
Starts voice_command_loop, agent_loop, process_monitor, TTS warmup,
launch greeting, and Puppeteer control-plane warm-start.
"""

import asyncio
import time

_launch_announced = False


async def _wait_for_desktop(timeout: float = 25.0) -> None:
    """Hold the greeting until the desktop window is connected, if it is coming."""
    from services.shutdown import is_shutting_down
    from services.websocket_manager import manager

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if is_shutting_down():
            return
        if manager.active_connections:
            await asyncio.sleep(0.8)
            return
        await asyncio.sleep(0.25)


async def _announce_online():
    """Speak the time-of-day launch line once the voice engine is ready."""
    global _launch_announced
    if _launch_announced:
        return

    from services.shutdown import is_shutting_down
    if is_shutting_down():
        return

    try:
        from brain.memory import save_memory
        from brain.personality import launch_greeting_line
        from brain.settings import is_muted
        from services.runtime_state import SystemState, flags
        from services.event_bus import event_bus, BusEvent, EventType
        from services.voice_loop import set_state
        from services.websocket_manager import manager
        from tts.hybrid_tts import speak_hybrid

        # Claim the voice session before the greeting starts. This prevents
        # the background wake-word detector from opening a second microphone
        # stream and transcribing the greeting through speaker echo.
        flags.continuous_voice_mode = True
        flags.stop_listen_trigger = False
        flags.force_listen_trigger = False
        flags.voice_session_active = False
        flags.listen_not_before = time.monotonic() + 30.0

        line = launch_greeting_line()
        flags.last_assistant_response = line
        flags.last_intent = "launch"
        try:
            save_memory("last_assistant_response", line)
            save_memory("last_intent", "launch")
        except Exception:
            pass

        await _wait_for_desktop()
        if is_shutting_down():
            return

        _launch_announced = True
        print(f"[Startup] Launch greeting: {line}")
        await manager.broadcast_chat(line)
        await manager.broadcast_json({"type": "system_ready"})

        if not is_muted():
            # Keep the standalone wake detector off for the whole greeting;
            # otherwise it can transcribe Jarvis's own speaker output.
            flags.listen_not_before = time.monotonic() + 30.0
            await set_state(SystemState.SPEAKING)
            await speak_hybrid(line, is_smart=False, response_id="launch_greeting")

        if not is_shutting_down():
            await set_state(SystemState.IDLE)
            # The launch greeting is the hand-off into a voice session. This
            # makes the first command available immediately after the line
            # finishes, without requiring another click or wake word.
            flags.continuous_voice_mode = True
            flags.stop_listen_trigger = False
            flags.force_listen_trigger = False
            flags.voice_session_active = False
            flags.stop_event.clear()
            # Let the speaker echo decay before the first microphone window.
            flags.listen_not_before = time.monotonic() + 1.5
            await event_bus.emit(BusEvent(EventType.WAKE))
            print("[Startup] Launch greeting complete — listening is active.")
    except Exception as e:
        print(f"[Startup] Launch greeting failed: {e}")


async def _boot_sequence():
    """Warm the local voice and greet after backend startup."""
    await asyncio.sleep(1.2)
    await _warm_up_tts()
    await _announce_online()


def _start_process_monitor():
    try:
        from executor.process_monitor import start_process_monitor
        start_process_monitor()
    except Exception as e:
        print(f"[Startup] Process Monitor failed to start: {e}")


async def _warm_up_tts():
    try:
        from tts.pocket_tts import warm_up_tts
        await asyncio.to_thread(warm_up_tts)
    except Exception as e:
        print(f"[Startup] Pocket TTS warm-up failed: {e}")


def _check_macos_runtime():
    from llm.ollama_client import describe
    print(f"[Startup] Platform: macOS / local STT / Pocket TTS / {describe()}")


async def start_all_services():
    """Called during FastAPI lifespan to start all background services."""
    _check_macos_runtime()
    try:
        from memory.db import init_db
        init_db()
        from brain.memory_store import init_store
        init_store()
        print("[Startup] Local SQLite WAL store ready.")
    except Exception as e:
        print(f"[Startup] Memory store failed to start: {e}")

    try:
        from services.terminal_stream import install as install_terminal_stream
        install_terminal_stream()
        print("[Startup] Terminal transparency stream wired (§7).")
    except Exception as e:
        print(f"[Startup] Terminal stream failed to install: {e}")

    from executor.agent_loop import agent_loop
    asyncio.create_task(agent_loop.run())

    _start_process_monitor()

    from services.voice_loop import voice_command_loop
    asyncio.create_task(voice_command_loop())

    # Voice warmup + launch greeting first. Puppeteer is optional after that.
    # Running them as a later task keeps /health responsive during model load.
    asyncio.create_task(_boot_sequence())
