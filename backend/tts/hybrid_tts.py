"""
hybrid_tts.py — Hybrid Voice Controller
=======================================
Routes speech to either Pocket TTS (Fast Commands) or Edge TTS (Smart Replies).
"""
import asyncio

# Global state for single execution enforcement
_is_speaking = False
_current_response_id = None

async def speak_hybrid(text: str, is_smart: bool = False, response_id: str = None):
    """
    Ensures TTS runs exactly once per response.
    """
    global _is_speaking, _current_response_id
    
    # STEP 3: BLOCK EMPTY / PARTIAL CALLS
    clean_text = text.strip() if text else ""
    if not clean_text or len(clean_text) < 5 or clean_text in ["...", "."]:
        print(f"[Hybrid TTS] Blocked: Invalid response length or content.")
        return

    # STEP 2: TTS ENTRY GUARD
    if _is_speaking:
        print(f"[Hybrid TTS] Blocked: Already speaking. Active response ID: {_current_response_id}")
        return

    print(f"[Hybrid TTS] Guard Check - incoming: {response_id}, current: {_current_response_id}")
    if response_id and response_id == _current_response_id:
        print(f"[Hybrid TTS] Blocked: Duplicate response ID {response_id} matches current {_current_response_id}")
        return

    from brain.settings import is_muted
    if is_muted():
        return

    try:
        _is_speaking = True
        _current_response_id = response_id
        
        print(f"[Hybrid TTS] Speaking: {clean_text[:50]}... with ID {response_id}")
        from tts.pocket_tts import speak as pocket_speak
        await asyncio.to_thread(pocket_speak, clean_text)
    except Exception as e:
        print(f"[Hybrid TTS] Execution failed: {e}")
    finally:
        _is_speaking = False

