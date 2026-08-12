"""
stt.py — 3-Layer VAD & Worker STT Pipeline for JARVIS
======================================================
Groq Whisper (whisper-large-v3) as primary engine.
Local Faster-Whisper (base.en) fallback when Groq is unavailable.

Layers:
1. sounddevice InputStream: Captures 30ms frames (16 kHz, mono, int16).
2. WebRTC VAD: Gates speech vs silence.
3. Queue & Worker Thread: Off-thread transcription, partial updates.
"""

import numpy as np
import sounddevice as sd
import webrtcvad
import queue
import time
import math
import struct
import threading
import io
import wave

from config import settings
from stt.correct import correct_transcript
from stt.filter import is_phantom_transcript, is_whisper_hallucination

# ── Configuration ────────────────────────────────────────────────────────────
SAMPLE_RATE = 16000
FRAME_DURATION = 30  # ms
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION / 1000)  # 480 samples
VAD_MODE = 1  # 0-3 (1 = balanced; 3 was clipping words)

SILENCE_LIMIT_FRAMES = 70     # 70 * 30ms = 2.1s silence ends command
INITIAL_TIMEOUT_FRAMES = 200  # 200 * 30ms = 6s no-speech timeout
PARTIAL_INTERVAL = 1.0        # Emit partial every 1s (less noise on early audio)
MIN_PARTIAL_FRAMES = 40       # Need ~1.2s speech before partial Groq call
MIN_SPEECH_FRAMES = 8         # Ignore clips shorter than ~240ms

STT_PROMPT = (
    "JARVIS voice assistant. Transcribe only what the user actually says. "
    "Never output subtitle credits, Amara.org, website URLs, video outros, "
    "or phrases the user did not speak. "
    "Commands: play music, stop music, read headlines, set volume, reduce volume, "
    "search for laptops, what is niacinamide, what is the time, tell me a joke, "
    "open Chrome, WhatsApp, Spotify, YouTube Music."
)

# ── Groq Client (lazy singleton) ────────────────────────────────────────────
_groq_client = None


def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        try:
            from groq import Groq
            api_key = settings.GROQ_API_KEY
            _groq_client = Groq(api_key=api_key)
            print("[STT] Groq client initialized.")
        except Exception as e:
            print(f"[STT] Groq client init failed: {e}")
    return _groq_client


def pcm_to_wav(pcm_bytes: bytes) -> bytes:
    """Convert raw 16-bit PCM to WAV in-memory for Groq API upload."""
    data_len = len(pcm_bytes)
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF',
        36 + data_len,
        b'WAVE',
        b'fmt ',
        16,
        1,  # PCM
        1,  # mono
        SAMPLE_RATE,
        SAMPLE_RATE * 2,  # byte rate
        2,  # block align
        16,  # bits per sample
        b'data',
        data_len,
    )
    return header + pcm_bytes


def _resample_pcm16(audio: np.ndarray, src_rate: int, dst_rate: int = SAMPLE_RATE) -> np.ndarray:
    if src_rate == dst_rate or len(audio) == 0:
        return audio.astype(np.int16, copy=False)
    dst_len = max(1, int(round(len(audio) * dst_rate / src_rate)))
    resampled = np.interp(
        np.linspace(0, len(audio) - 1, dst_len, dtype=np.float64),
        np.arange(len(audio), dtype=np.float64),
        audio.astype(np.float64),
    )
    return np.clip(resampled, -32768, 32767).astype(np.int16)


def wav_to_pcm(wav_bytes: bytes) -> bytes:
    """Convert PCM WAV bytes into 16 kHz mono signed 16-bit PCM."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        frames = wav_file.readframes(wav_file.getnframes())

    if not frames:
        return b""

    if sample_width == 1:
        audio = (np.frombuffer(frames, dtype=np.uint8).astype(np.int16) - 128) << 8
    elif sample_width == 2:
        audio = np.frombuffer(frames, dtype=np.int16)
    elif sample_width == 4:
        audio = (np.frombuffer(frames, dtype=np.int32) >> 16).astype(np.int16)
    else:
        raise ValueError(f"Unsupported WAV sample width: {sample_width}")

    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1).astype(np.int16)

    return _resample_pcm16(audio, sample_rate).tobytes()


def ensure_pcm_audio(audio_bytes: bytes) -> bytes:
    """Accept raw PCM or PCM WAV and return 16 kHz mono signed 16-bit PCM."""
    if not audio_bytes:
        return b""
    if audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE":
        return wav_to_pcm(audio_bytes)
    return audio_bytes


def normalize_pcm(pcm_bytes: bytes) -> bytes:
    """Boost quiet mic input so Whisper picks up speech more reliably."""
    if not pcm_bytes:
        return pcm_bytes
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if peak < 800:
        return pcm_bytes
    target = 22000.0
    scaled = np.clip(audio * (target / peak), -32768, 32767).astype(np.int16)
    return scaled.tobytes()


def transcribe_groq(pcm_bytes: bytes) -> str:
    """Send audio to Groq whisper-large-v3 and return transcribed text."""
    client = _get_groq_client()
    if client is None:
        return ""

    pcm_bytes = ensure_pcm_audio(pcm_bytes)
    pcm_bytes = normalize_pcm(pcm_bytes)
    wav_bytes = pcm_to_wav(pcm_bytes)
    try:
        result = client.audio.transcriptions.create(
            file=("audio.wav", wav_bytes),
            model="whisper-large-v3",
            language="en",
            temperature=0.0,
            prompt=STT_PROMPT,
        )
        return result.text.strip()
    except Exception as e:
        print(f"[STT Groq] Transcription error: {e}")
        return ""


# ── Local Whisper Fallback ───────────────────────────────────────────────────
_model = None
_model_lock = threading.Lock()


def _get_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        try:
            from faster_whisper import WhisperModel
            print("[STT] Loading local fallback model (base.en)…")
            _model = WhisperModel("base.en", device="cpu", compute_type="int8")
            print("[STT] Local fallback ready.")
        except Exception as e:
            print(f"[STT] Local fallback unavailable: {e}")
            _model = None
    return _model


def transcribe_local(pcm_bytes: bytes) -> str:
    model = _get_model()
    if model is None or not pcm_bytes:
        return ""
    pcm_bytes = ensure_pcm_audio(pcm_bytes)
    pcm_bytes = normalize_pcm(pcm_bytes)
    audio_array = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    try:
        segments, _ = model.transcribe(
            audio_array,
            beam_size=5,
            language="en",
            initial_prompt=STT_PROMPT,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
            condition_on_previous_text=False,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()
    except Exception as e:
        print(f"[STT Local] Transcribe error: {e}")
        return ""


def transcribe_audio(pcm_bytes: bytes) -> str:
    """Primary Groq transcription with local fallback; reject hallucinations."""
    if not pcm_bytes:
        return ""
    pcm_bytes = ensure_pcm_audio(pcm_bytes)
    text = transcribe_groq(pcm_bytes)
    if not text or is_whisper_hallucination(text):
        local = transcribe_local(pcm_bytes)
        if local and not is_whisper_hallucination(local):
            text = local
        elif is_whisper_hallucination(text):
            text = ""
    text = correct_transcript(text)
    if is_phantom_transcript(text):
        return ""
    return text


def listen_stream(partial_cb=None, stop_event=None) -> str:
    """
    Blocks while listening. Captures chunks, pushes them to a worker thread.
    Returns the final transcribed text when silence or timeout is reached.
    """
    vad = webrtcvad.Vad(VAD_MODE)
    audio_queue = queue.Queue()

    done_event = threading.Event()
    final_result = [""]
    last_ui_text = [""]

    def stt_worker():
        while True:
            item = audio_queue.get()
            if item["type"] == "quit":
                done_event.set()
                break

            if item["type"] == "partial":
                try:
                    while True:
                        next_item = audio_queue.get_nowait()
                        if next_item["type"] == "final":
                            item = next_item
                            break
                        elif next_item["type"] == "partial":
                            item = next_item
                        elif next_item["type"] == "quit":
                            item = next_item
                            break
                except queue.Empty:
                    pass

            if item["type"] == "quit":
                done_event.set()
                break

            audio_bytes = item["data"]
            if not audio_bytes:
                if item["type"] == "final":
                    final_result[0] = last_ui_text[0]
                    done_event.set()
                    break
                continue

            # Skip partial Groq calls on very short audio — early partials are inaccurate
            if item["type"] == "partial" and len(audio_bytes) < MIN_PARTIAL_FRAMES * FRAME_SIZE * 2:
                continue

            text = transcribe_audio(audio_bytes)

            if item["type"] == "partial":
                current_countdown = item.get("countdown")
                if partial_cb and (text != last_ui_text[0] or current_countdown is not None):
                    try:
                        partial_cb(text, countdown=current_countdown)
                        if text:
                            last_ui_text[0] = text
                    except Exception:
                        pass
            elif item["type"] == "final":
                final_result[0] = text if text.strip() else correct_transcript(last_ui_text[0])
                done_event.set()
                break

    worker_thread = threading.Thread(target=stt_worker, daemon=True)
    worker_thread.start()

    stream = None
    try:
        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME_SIZE,
        )
        with stream:
            buffer = []
            silence_counter = 0
            last_partial_time = time.time()
            has_spoken = False
            last_countdown = None

            print("[STT Pipeline] Stream active. VAD gating is ON.")

            while not done_event.is_set():
                if stop_event and stop_event.is_set():
                    print("[STT Pipeline] External stop trigger intercepted.")
                    audio_queue.put({"type": "final", "data": b"".join(buffer)})
                    break

                try:
                    frame_array, overflowed = stream.read(FRAME_SIZE)
                    frame = frame_array.tobytes()
                except Exception as e:
                    print(f"[STT Pipeline] Mic read error: {e}")
                    continue

                is_speech = vad.is_speech(frame, SAMPLE_RATE)

                if is_speech:
                    buffer.append(frame)
                    silence_counter = 0
                    has_spoken = True
                else:
                    silence_counter += 1
                    if has_spoken:
                        buffer.append(frame)

                if has_spoken:
                    now = time.time()
                    total_sil_limit = (SILENCE_LIMIT_FRAMES * FRAME_DURATION) / 1000.0
                    current_sil_s = (silence_counter * FRAME_DURATION) / 1000.0
                    countdown_val = (
                        int(math.ceil(total_sil_limit - current_sil_s))
                        if 0.5 < current_sil_s <= total_sil_limit
                        else None
                    )

                    if (now - last_partial_time > PARTIAL_INTERVAL) or (
                        countdown_val is not None and countdown_val != last_countdown
                    ):
                        audio_queue.put({
                            "type": "partial",
                            "data": b"".join(buffer),
                            "countdown": countdown_val,
                        })
                        last_partial_time = now
                        last_countdown = countdown_val

                    if silence_counter > SILENCE_LIMIT_FRAMES or len(buffer) >= 800:
                        if len(buffer) < MIN_SPEECH_FRAMES:
                            print("[STT Pipeline] Clip too short — ignoring.")
                            audio_queue.put({"type": "final", "data": b""})
                            break
                        print(f"[STT Pipeline] End of Speech ({silence_counter * FRAME_DURATION}ms). Buffer: {len(buffer)}")
                        if partial_cb:
                            try:
                                partial_cb(last_ui_text[0], countdown=0)
                            except Exception:
                                pass
                        audio_queue.put({"type": "final", "data": b"".join(buffer)})
                        break
                else:
                    if silence_counter > INITIAL_TIMEOUT_FRAMES:
                        print("[STT Pipeline] Initial timeout. No speech detected.")
                        audio_queue.put({"type": "final", "data": b""})
                        break

    except Exception as e:
        print(f"[STT Pipeline] Mic Stream Crash: {e}")
        audio_queue.put({"type": "quit"})
        done_event.set()

    done_event.wait(timeout=8.0)
    audio_queue.put({"type": "quit"})
    worker_thread.join(timeout=2.0)

    final_text = final_result[0].strip()
    print(f"[STT Pipeline] Completed: {final_text!r}")
    return final_text


if __name__ == "__main__":
    def on_partial(text, countdown=None):
        print(f"\r[Live] {text}   ", end="", flush=True)

    try:
        print("[Demo] Listening...\n")
        res = listen_stream(partial_cb=on_partial)
        print(f"\n[Done] {res}")
    except KeyboardInterrupt:
        print("\nShutdown.")
