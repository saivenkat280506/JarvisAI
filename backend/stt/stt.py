"""
stt.py — 3-Layer VAD & Worker STT Pipeline for JARVIS
======================================================
Local faster-whisper (English-only), with no audio upload path.

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
import re
import threading
import io
import wave
import os

from stt.correct import correct_transcript
from stt.devices import pick_input_device
from stt.filter import is_phantom_transcript, is_whisper_hallucination

# ── Configuration ────────────────────────────────────────────────────────────
SAMPLE_RATE = 16000
FRAME_DURATION = 30  # ms
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION / 1000)  # 480 samples
VAD_MODE = 1  # 0-3 (1 = balanced; 3 was clipping words)

SILENCE_LIMIT_FRAMES = 40     # 1.2s silence ends command
INITIAL_TIMEOUT_FRAMES = 200  # 200 * 30ms = 6s no-speech timeout
PARTIAL_INTERVAL = 2.0        # Avoid repeated local inference calls
MIN_PARTIAL_FRAMES = 60       # Need ~1.8s speech before a partial call
MAX_PARTIAL_TRANSCRIPTIONS = 1  # One preview; final transcription is authoritative
MIN_SPEECH_FRAMES = 8         # Ignore clips shorter than ~240ms

# ── Local Whisper model (lazy singleton) ────────────────────────────────────
_local_model = None
_local_model_lock = threading.Lock()


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


def _pcm_stats(pcm_bytes: bytes) -> tuple[float, float]:
    if not pcm_bytes:
        return 0.0, 0.0
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    if audio.size == 0:
        return 0.0, 0.0
    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(audio ** 2)))
    return peak, rms


def is_audible_speech(pcm_bytes: bytes) -> bool:
    """Reject near-silent clips that make Whisper invent 'give me a minute'."""
    peak, rms = _pcm_stats(pcm_bytes)
    return peak >= 1200 and rms >= 220


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


def _get_local_model():
    """Load the configured local English Whisper model once."""
    global _local_model
    if _local_model is not None:
        return _local_model
    with _local_model_lock:
        if _local_model is not None:
            return _local_model
        try:
            from faster_whisper import WhisperModel
            model_name = os.environ.get("JARVIS_LOCAL_WHISPER_MODEL", "base.en").strip().lower()
            if model_name not in {"base.en", "small.en"}:
                print(f"[STT] Unsupported model {model_name!r}; using base.en (English-only).")
                model_name = "base.en"
            print(f"[STT] Loading local English Whisper ({model_name})…")
            _local_model = WhisperModel(model_name, device="cpu", compute_type="int8")
            print("[STT] Local Whisper ready.")
        except Exception as exc:
            print(f"[STT] Local Whisper unavailable: {exc}")
            _local_model = None
    return _local_model


def transcribe_local(pcm_bytes: bytes) -> str:
    model = _get_local_model()
    if model is None:
        return ""
    try:
        pcm_bytes = normalize_pcm(pcm_bytes)
        segments, _ = model.transcribe(
            np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0,
            language="en",
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
            initial_prompt="The assistant is named Jarvis.",
        )
        return " ".join((segment.text or "").strip() for segment in segments).strip()
    except Exception as exc:
        print(f"[STT Local] Transcription error: {exc}")
        return ""


def _is_empty_transcript(text: str) -> bool:
    if not text:
        return True
    return bool(re.fullmatch(r"[\s.,!?…'\"\-–—]*", text))


def transcribe_audio(pcm_bytes: bytes) -> str:
    """Transcribe locally in English; reject hallucinations and silent clips."""
    if not pcm_bytes:
        return ""
    pcm_bytes = ensure_pcm_audio(pcm_bytes)
    if not is_audible_speech(pcm_bytes):
        return ""
    text = transcribe_local(pcm_bytes)
    if _is_empty_transcript(text) or is_whisper_hallucination(text):
        return ""
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
        partial_count = 0
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

            # Skip partial inference on very short audio — early results are inaccurate.
            if item["type"] == "partial" and len(audio_bytes) < MIN_PARTIAL_FRAMES * FRAME_SIZE * 2:
                continue
            if item["type"] == "partial" and partial_count >= MAX_PARTIAL_TRANSCRIPTIONS:
                continue

            text = transcribe_audio(audio_bytes)
            if item["type"] == "partial":
                partial_count += 1

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
        mic = pick_input_device()
        stream = sd.InputStream(
            device=mic,
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


def listen_vad(on_speech_start=None, on_speech_end=None, stop_event=None, timeout: float = 20.0) -> str:
    """
    Capture and transcribe speech using the neural Silero VAD + faster-whisper pipeline.
    Includes rolling 300ms pre-speech buffer, 600ms silence threshold, and barge-in support.
    """
    from stt.vad import JarvisVADPipeline
    result = [""]
    done_event = threading.Event()

    def _on_transcript(text: str):
        result[0] = text
        done_event.set()

    pipeline = JarvisVADPipeline(
        on_speech_start=on_speech_start,
        on_speech_end=on_speech_end,
        on_transcript=_on_transcript,
    )
    pipeline.start()

    try:
        start_time = time.time()
        while not done_event.is_set():
            if stop_event and stop_event.is_set():
                break
            if time.time() - start_time > timeout:
                break
            time.sleep(0.05)
    finally:
        pipeline.stop()

    return result[0].strip()


if __name__ == "__main__":
    def on_partial(text, countdown=None):
        print(f"\r[Live] {text}   ", end="", flush=True)

    try:
        print("[Demo] Listening...\n")
        res = listen_stream(partial_cb=on_partial)
        print(f"\n[Done] {res}")
    except KeyboardInterrupt:
        print("\nShutdown.")
