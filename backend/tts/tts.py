"""
tts.py — JARVIS TTS Interface
=============================
Provides a working text-to-speech interface.
"""
import os
import asyncio
import pygame
import tempfile
from edge_tts import Communicate

# Pre-initialize pygame mixer for immediate playback
try:
    pygame.mixer.pre_init(44100, -16, 2, 512)
    pygame.mixer.init()
except Exception as e:
    print(f"[TTS] Mixer Init Error: {e}")

async def speak(text: str):
    """
    Synthesizes text to speech using edge-tts and plays it immediately.
    """
    from brain.settings import is_muted
    if is_muted():
        print("[TTS] Muted. Skipping playback.")
        return

    if not text or len(text.strip()) == 0:
        return

    # Use a temporary file for the audio output
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_file:
        temp_mp3 = tmp_file.name

    try:
        # Generate audio using edge-tts
        communicate = Communicate(text, "en-US-GuyNeural")
        await communicate.save(temp_mp3)
        
        # Play the resulting audio using pygame (synchronous)
        pygame.mixer.music.load(temp_mp3)
        pygame.mixer.music.play()
        
        # Wait for playback to finish without blocking the event loop
        while pygame.mixer.music.get_busy():
            await asyncio.sleep(0.05)
            
        pygame.mixer.music.unload()
    except Exception as e:
        print(f"[TTS] Synthesis Error: {e}")
    finally:
        # Cleanup temporary file
        try:
            if os.path.exists(temp_mp3):
                os.remove(temp_mp3)
        except Exception:
            pass


if __name__ == "__main__":
    async def test():
        print("Testing Edge TTS...")
        await speak("Systems online. I am ready for your command, sir.")
        print("Playback complete.")
    
    asyncio.run(test())
