"""
shutdown.py — Graceful JARVIS Shutdown
======================================
Terminates voice, TTS, music, agents, background tasks, and monitors on app close.
"""

import asyncio

from services.runtime_state import flags, SystemState

_shutdown_event = asyncio.Event()
_shutdown_lock = asyncio.Lock()
_is_shutting_down = False


def is_shutting_down() -> bool:
    return _is_shutting_down or flags.shutdown_requested


def get_shutdown_event() -> asyncio.Event:
    return _shutdown_event


async def shutdown_all_services(reason: str = "app_close") -> dict:
    """Stop every JARVIS background subsystem cleanly."""
    global _is_shutting_down

    async with _shutdown_lock:
        if _is_shutting_down:
            return {"status": "already_shutdown", "reason": reason}

        _is_shutting_down = True
        flags.shutdown_requested = True
        print(f"[Shutdown] Initiating full shutdown ({reason})...")

        # ── Voice session + STT ─────────────────────────────────────────────
        flags.reset_processing_state()
        flags.continuous_voice_mode = False
        flags.voice_session_active = False
        flags.stop_listen_trigger = True
        flags.is_listening = False
        flags.is_processing = False
        flags.stop_event.set()

        # ── TTS ───────────────────────────────────────────────────────────────
        try:
            from tts.hybrid_tts import force_stop_all_tts
            force_stop_all_tts()
        except Exception as exc:
            print(f"[Shutdown] TTS stop error: {exc}")

        # ── Local music ─────────────────────────────────────────────────────
        try:
            from executor.music_services import stop_local_music
            stop_local_music()
        except Exception as exc:
            print(f"[Shutdown] Music stop error: {exc}")

        # ── Web / OS agents ─────────────────────────────────────────────────
        try:
            from executor.web_agent import request_stop, clear_stop
            request_stop()
            clear_stop()
        except Exception as exc:
            print(f"[Shutdown] Web agent stop error: {exc}")

        # ── Background asyncio tasks ────────────────────────────────────────
        task_count = 0
        try:
            from executor.task_manager import task_manager
            for tid in list(task_manager.active_tasks.keys()):
                if task_manager.cancel_task(tid):
                    task_count += 1
        except Exception as exc:
            print(f"[Shutdown] Task cancel error: {exc}")

        # ── Agent retry loop ────────────────────────────────────────────────
        try:
            from executor.agent_loop import agent_loop
            agent_loop.is_running = False
            agent_loop.retry_queue.clear()
        except Exception as exc:
            print(f"[Shutdown] Agent loop stop error: {exc}")

        # ── Process monitor thread ──────────────────────────────────────────
        try:
            from executor.process_monitor import stop_process_monitor
            stop_process_monitor()
        except Exception as exc:
            print(f"[Shutdown] Process monitor stop error: {exc}")

        # ── Unblock voice loop / wake detector waits ────────────────────────
        try:
            from services.voice_loop import _wake_signal
            _wake_signal.set()
        except Exception as exc:
            print(f"[Shutdown] Wake signal error: {exc}")

        try:
            from services.event_bus import event_bus, BusEvent, EventType
            await event_bus.emit(BusEvent(EventType.STOP))
        except Exception as exc:
            print(f"[Shutdown] Event bus stop error: {exc}")

        _shutdown_event.set()

        # ── Broadcast idle + close sockets ────────────────────────────────
        try:
            from services.voice_loop import set_state
            await set_state(SystemState.IDLE)
        except Exception as exc:
            print(f"[Shutdown] State broadcast error: {exc}")

        try:
            from services.websocket_manager import manager
            await manager.broadcast_json({"type": "shutdown", "reason": reason})
            await manager.close_all()
        except Exception as exc:
            print(f"[Shutdown] WebSocket close error: {exc}")

        print(f"[Shutdown] Complete — cancelled {task_count} background task(s).")
        return {"status": "shutdown", "cancelled_tasks": task_count, "reason": reason}