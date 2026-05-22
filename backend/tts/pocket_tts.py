import os
import re
import threading
from pathlib import Path

import numpy as np
import sounddevice as sd
from pocket_tts import TTSModel

VOICE_PROMPT = Path(__file__).with_name("voices") / "jarvis voice.wav"

_model = None
_voice_state = None
_hardware_sample_rate = None
_model_lock = threading.Lock()
_playback_lock = threading.Lock()
_stop_event = threading.Event()
_is_speaking = False
_active_stream = None

def _get_hardware_sample_rate():
    global _hardware_sample_rate
    if _hardware_sample_rate is not None:
        return _hardware_sample_rate
    try:
        device_info = sd.query_devices(kind='output')
        _hardware_sample_rate = int(device_info['default_samplerate'])
    except Exception:
        _hardware_sample_rate = 44100
    return _hardware_sample_rate

def _resample_audio(audio, original_rate, target_rate):
    if original_rate == target_rate:
        return audio
    duration = len(audio) / original_rate
    num_samples = int(duration * target_rate)
    return np.interp(
        np.linspace(0, len(audio), num_samples, endpoint=False),
        np.arange(len(audio)),
        audio.flatten()
    ).reshape(-1, 1).astype(np.float32)


def clean_text_for_speech(text: str) -> str:
    """Trim formatting noise so the cloned voice stays natural."""
    # Normalize acronyms and abbreviations for natural speech
    text = re.sub(r"\bJ\.?A\.?R\.?V\.?I\.?S\b\.?", "Jarvis", text, flags=re.IGNORECASE)
    text = re.sub(r"\ba\.?k\.?a\b\.?", "also known as", text, flags=re.IGNORECASE)

    text = re.sub(r"\*+", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"^\s*[\-\*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = text.replace("`", "").replace("#", "")
    text = re.sub(r"https?://\S+", "", text)
    if "{" in text and "}" in text:
        text = re.sub(r"\{[^\}]+\}", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _ensure_model_loaded():
    global _model, _voice_state
    if _model is not None and _voice_state is not None:
        return

    with _model_lock:
        if _model is not None and _voice_state is not None:
            return

        if not VOICE_PROMPT.exists():
            raise FileNotFoundError(f"Missing voice prompt at {VOICE_PROMPT}")

        model = TTSModel.load_model()
        voice_state = model.get_state_for_audio_prompt(VOICE_PROMPT)
        _model = model
        _voice_state = voice_state


def warm_up_tts():
    """Load the model and voice state ahead of the first spoken response."""
    try:
        _ensure_model_loaded()
        print("[TTS] Pocket TTS warmed with Jarvis voice prompt.")
    except Exception as exc:
        print(f"[TTS Warmup Error] {exc}")


def _chunk_to_samples(chunk) -> np.ndarray:
    if hasattr(chunk, "detach"):
        chunk = chunk.detach().cpu().numpy()
    samples = np.asarray(chunk, dtype=np.float32).reshape(-1, 1)
    return np.clip(samples, -1.0, 1.0)


def speak(text: str):
    """Generate low-latency local speech from the Jarvis voice prompt."""
    global _is_speaking, _active_stream

    clean_text = clean_text_for_speech(text)
    if len(clean_text) < 2:
        return

    try:
        _ensure_model_loaded()
    except Exception as exc:
        print(f"[TTS Load Error] {exc}")
        return

    with _playback_lock:
        _stop_event.clear()
        _is_speaking = True

        stream = None
        try:
            target_rate = _get_hardware_sample_rate()
            print(f"[TTS] Hardware Rate: {target_rate}Hz, Model Rate: {_model.sample_rate}Hz")
            
            stream = sd.OutputStream(
                samplerate=target_rate,
                channels=1,
                dtype="float32",
                latency="low",
                blocksize=0,
            )
            _active_stream = stream
            stream.start()

            # Split clean_text into sentences
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', clean_text) if s.strip()]
            if not sentences:
                return

            import queue
            audio_queue = queue.Queue(maxsize=10)

            def producer():
                try:
                    for sentence in sentences:
                        if _stop_event.is_set():
                            break
                        print(f"[TTS Producer] Generating audio for sentence: '{sentence}'")
                        for audio_chunk in _model.generate_audio_stream(
                            model_state=_voice_state,
                            text_to_generate=sentence,
                            copy_state=True,
                        ):
                            if _stop_event.is_set():
                                break
                            samples = _chunk_to_samples(audio_chunk)
                            samples = _resample_audio(samples, _model.sample_rate, target_rate)
                            
                            # Put to queue, but check for stop event to avoid hanging
                            while not _stop_event.is_set():
                                try:
                                    audio_queue.put(samples, timeout=0.1)
                                    break
                                except queue.Full:
                                    continue
                        if _stop_event.is_set():
                            break
                except Exception as e:
                    print(f"[TTS Producer Error] {e}")
                finally:
                    # Put sentinel to signal end of generation
                    audio_queue.put(None)

            producer_thread = threading.Thread(target=producer, daemon=True)
            producer_thread.start()

            while not _stop_event.is_set():
                try:
                    chunk = audio_queue.get(timeout=0.1)
                except queue.Empty:
                    # If producer is still alive, wait
                    if producer_thread.is_alive():
                        continue
                    else:
                        break

                if chunk is None:
                    # End of audio
                    break

                try:
                    stream.write(chunk)
                except Exception as e:
                    print(f"[TTS] Stream write failed (probably aborted): {e}")
                    break

            if stream.active:
                stream.stop()
        except Exception as exc:
            print(f"[TTS Audio Error] {exc}")
        finally:
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            _active_stream = None
            _is_speaking = False
            _stop_event.clear()


def stop_speech():
    """Stop any current Pocket TTS playback as quickly as possible."""
    global _active_stream, _is_speaking

    _stop_event.set()
    try:
        if _active_stream is not None:
            _active_stream.abort()
    except Exception as exc:
        print(f"[TTS Stop Error] {exc}")


def is_speaking() -> bool:
    return _is_speaking
