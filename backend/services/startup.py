"""
startup.py — Background Service Launcher for JARVIS
====================================================
Starts voice_command_loop, agent_loop, process_monitor, TTS warmup,
and Puppeteer control-plane warm-start (low latency first browser cmd).
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


async def _warm_puppeteer():
    """Pre-start Node Puppeteer server so first browser command is fast."""
    try:
        from executor.puppeteer_client import ensure_server

        ok = await asyncio.to_thread(ensure_server, True)
        print(f"[Startup] Puppeteer control plane ready={ok}")
    except Exception as e:
        print(f"[Startup] Puppeteer warm-up failed (will start on demand): {e}")


async def start_all_services():
    """Called during FastAPI lifespan to start all background services."""
    from executor.agent_loop import agent_loop
    asyncio.create_task(agent_loop.run())

    _start_process_monitor()

    from services.voice_loop import voice_command_loop
    asyncio.create_task(voice_command_loop())

    asyncio.create_task(_warm_up_tts())
    asyncio.create_task(_warm_puppeteer())
