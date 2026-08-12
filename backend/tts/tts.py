"""
tts.py — Edge TTS playback (fast cloud synthesis for longer replies)
"""
import asyncio
import os
import tempfile

import pygame
from edge_tts import Communicate

EDGE_VOICE = "en-US-GuyNeural"

_stop_event = asyncio.Event()

try:
    pygame.mixer.pre_init(44100, -16, 2, 512)
    pygame.mixer.init()
except Exception as e:
    print(f"[TTS] Mixer Init Error: {e}")


def stop_edge_speech():
    _stop_event.set()
    try:
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
    except Exception:
        pass


def _duck_local_music():
    try:
        from executor.music_services import duck_for_speech
        duck_for_speech()
    except Exception:
        pass


def _restore_local_music():
    try:
        from executor.music_services import resume_after_speech
        resume_after_speech()
    except Exception:
        pass


async def speak(text: str):
    from brain.settings import is_muted

    if is_muted():
        return
    if not text or len(text.strip()) < 2:
        return

    _stop_event.clear()
    _duck_local_music()

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_file:
        temp_mp3 = tmp_file.name

    try:
        await Communicate(text, EDGE_VOICE).save(temp_mp3)
        if _stop_event.is_set():
            return

        pygame.mixer.music.load(temp_mp3)
        pygame.mixer.music.play()

        while pygame.mixer.music.get_busy():
            if _stop_event.is_set():
                pygame.mixer.music.stop()
                break
            await asyncio.sleep(0.03)

        pygame.mixer.music.unload()
    except Exception as e:
        print(f"[Edge TTS] Synthesis Error: {e}")
    finally:
        _restore_local_music()
        try:
            if os.path.exists(temp_mp3):
                os.remove(temp_mp3)
        except Exception:
            pass
        _stop_event.clear()