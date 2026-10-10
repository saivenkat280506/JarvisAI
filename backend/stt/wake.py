"""
wake.py — Wake Word Detection for JARVIS
========================================
Local WebRTC VAD first, then local English Whisper transcription.
"""

import numpy as np
import sounddevice as sd
import queue
import webrtcvad
from stt.devices import pick_input_device
from stt.stt import SAMPLE_RATE, transcribe_audio

CHUNK_DURATION = 1.6
FRAME_MS = 30
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_MS / 1000)
RMS_GATE = 0.028
MIN_SPEECH_FRAMES = 10  # ~300ms of VAD speech before local transcription

WAKE_PHRASES = ["jarvis", "hey jarvis", "wake up jarvis", "wake jarvis"]
_WAKE_ALIASES = (
    "jarvis", "harvis", "hervis", "jervis", "jarvish", "jadwish",
    "jarwish", "jarvus", "jarviz", "jarves",
)


def wait_for_wake_word(stop_check=None, barge_in_callback=None) -> bool:
    """
    Continuously listens for wake phrases using local Whisper.
    If stop_check() returns True, breaks early (UI trigger).
    """
    audio_queue: queue.Queue = queue.Queue(maxsize=30)
    vad = webrtcvad.Vad(2)

    def audio_callback(indata: np.ndarray, frames: int, cb_time, status):
        if audio_queue.full():
            try:
                audio_queue.get_nowait()
            except queue.Empty:
                pass
        audio_queue.put_nowait(indata.copy())

    try:
        with sd.InputStream(
            device=pick_input_device(),
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=2048,
            latency="high",
            callback=audio_callback,
        ):
            audio_buffer: list = []
            target_samples = int(SAMPLE_RATE * CHUNK_DURATION)

            while True:
                if stop_check and stop_check():
                    return True

                while len(audio_buffer) < target_samples:
                    try:
                        chunk = audio_queue.get(timeout=0.2)
                        audio_buffer.extend(chunk.flatten())
                    except queue.Empty:
                        if stop_check and stop_check():
                            return True
                        continue

                audio_data = np.array(audio_buffer[:target_samples], dtype=np.float32)
                audio_buffer = audio_buffer[-SAMPLE_RATE:]
                rms = float(np.sqrt(np.mean(np.square(audio_data)))) if audio_data.size else 0.0
                if rms < RMS_GATE:
                    continue

                pcm = np.clip(audio_data * 32767.0, -32768, 32767).astype(np.int16)
                speech_frames = 0
                raw = pcm.tobytes()
                frame_bytes = FRAME_SAMPLES * 2
                for i in range(0, len(raw) - frame_bytes + 1, frame_bytes):
                    try:
                        if vad.is_speech(raw[i:i + frame_bytes], SAMPLE_RATE):
                            speech_frames += 1
                    except Exception:
                        continue
                if speech_frames < MIN_SPEECH_FRAMES:
                    continue

                try:
                    text = transcribe_audio(raw).lower().strip()
                except Exception as e:
                    print(f"[Wake] Local transcription error: {e}")
                    continue

                if text and any(alias in text for alias in _WAKE_ALIASES):
                    print(f"[Wake] Wake phrase detected in: '{text}'")
                    return True

    except Exception as e:
        print(f"[Wake] Stream error: {e}")
        return False


if __name__ == "__main__":
    try:
        while True:
            if wait_for_wake_word():
                print("[System] Wake phrase triggered — ready for command.")
    except KeyboardInterrupt:
        print("\n[System] Shutdown.")
