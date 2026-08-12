"""
hybrid_tts.py — Voice Controller
================================
Always uses Pocket TTS (Jarvis cloned voice) for consistent speech.
"""
import asyncio

_is_speaking = False
_current_response_id = None


def force_stop_all_tts() -> None:
    global _is_speaking, _current_response_id
    from tts.pocket_tts import stop_speech

    stop_speech()
    _is_speaking = False
    _current_response_id = None
    print("[Hybrid TTS] Force-stopped all speech.")


async def speak_hybrid(text: str, is_smart: bool = False, response_id: str = None):
    global _is_speaking, _current_response_id

    from services.runtime_state import flags

    clean_text = text.strip() if text else ""
    # Allow short confirmations from STT tasks ("Done.", "Opened.", etc.)
    if not clean_text or len(clean_text) < 2 or clean_text in ["...", ".", ".."]:
        print("[Hybrid TTS] Blocked: Invalid response length or content.")
        return

    speak_epoch = flags.speak_epoch

    # Garage / home-arrival lines must never be silently dropped
    force_line = bool(response_id and ("garage_line" in str(response_id) or "daddys" in str(response_id)))

    if _is_speaking and not force_line:
        print(f"[Hybrid TTS] Blocked: Already speaking. Active response ID: {_current_response_id}")
        return

    if response_id and response_id == _current_response_id and not force_line:
        print(f"[Hybrid TTS] Blocked: Duplicate response ID {response_id}")
        return

    if force_line and _is_speaking:
        print("[Hybrid TTS] Forcing garage dialogue — stopping previous speech first")
        force_stop_all_tts()

    from brain.settings import is_muted
    if is_muted():
        return

    try:
        if flags.speak_epoch != speak_epoch:
            print("[Hybrid TTS] Blocked: stale speak epoch.")
            return

        _is_speaking = True
        _current_response_id = response_id

        print(f"[Hybrid TTS] Speaking (pocket): {clean_text[:50]}... (ID {response_id})")
        from tts.pocket_tts import speak as pocket_speak

        def _run_pocket_speak():
            from services.runtime_state import flags as runtime_flags
            if runtime_flags.speak_epoch != speak_epoch:
                return
            pocket_speak(clean_text)

        # Run TTS off the event loop so HTTP/WS stay responsive while audio streams
        await asyncio.to_thread(_run_pocket_speak)

    except Exception as e:
        print(f"[Hybrid TTS] Execution failed: {e}")
    finally:
        _is_speaking = False
        _current_response_id = None