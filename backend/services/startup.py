"""
startup.py — Background Service Launcher for JARVIS
====================================================
Starts voice_command_loop, agent_loop, process_monitor, and TTS warmup.
"""

import asyncio


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


async def start_all_services():
    """Called during FastAPI lifespan to start all background services."""
    from executor.agent_loop import agent_loop
    asyncio.create_task(agent_loop.run())

    _start_process_monitor()

    from services.voice_loop import voice_command_loop
    asyncio.create_task(voice_command_loop())

    asyncio.create_task(_warm_up_tts())
