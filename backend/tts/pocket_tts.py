"""
pocket_tts.py — JARVIS voice clone (Pocket TTS)
================================================
Quality-focused path:
  - Clean mono 24 kHz voice prompt (not the raw noisy long stereo sample)
  - Lower temperature + more LSD steps for clearer speech
  - Full-buffer synthesis + high-latency playback to avoid stream crackle
  - Light post-filter + fade + soft limiter
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

import numpy as np
import sounddevice as sd
from pocket_tts import TTSModel

VOICES_DIR = Path(__file__).with_name("voices")
# Prefer the cleaned clone prompt; fall back to original if clean missing.
VOICE_PROMPT_CLEAN = VOICES_DIR / "jarvis_voice_clean.wav"
VOICE_PROMPT_RAW = VOICES_DIR / "jarvis voice.wav"

_model = None
_voice_state = None
_playback_rate: int | None = None
_model_lock = threading.Lock()
_playback_lock = threading.Lock()
_stop_event = threading.Event()
_is_speaking = False

# Quality knobs (pocket-tts defaults: temp=0.7, lsd=1 → noisier)
_LOAD_TEMP = 0.45
_LOAD_LSD_STEPS = 5
_LOAD_NOISE_CLAMP = 1.8
_LOAD_EOS_THRESHOLD = -3.0


def _resolve_playback_rate(model_rate: int) -> int:
    global _playback_rate
    if _playback_rate is not None:
        return _playback_rate
    try:
        sd.check_output_settings(samplerate=model_rate, channels=1, dtype="float32")
        _playback_rate = model_rate
    except Exception:
        _playback_rate = int(sd.query_devices(kind="output")["default_samplerate"])
    return _playback_rate


def _resample_once(audio: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst or len(audio) == 0:
        return audio.astype(np.float32, copy=False)
    # Polyphase-ish via linear interp is OK for short speech; prefer direct 24k when possible.
    n = max(1, int(len(audio) * dst / src))
    out = np.interp(
        np.linspace(0, len(audio) - 1, n, dtype=np.float64),
        np.arange(len(audio), dtype=np.float64),
        audio.flatten().astype(np.float64),
    )
    return out.astype(np.float32)


def clean_text_for_speech(text: str) -> str:
    from tts.pronunciation import apply_pronunciation_fixes

    text = apply_pronunciation_fixes(text)
    text = re.sub(r"\bJ\.?A\.?R\.?V\.?I\.?S\b\.?", "Jarvis", text, flags=re.IGNORECASE)
    text = re.sub(r"\ba\.?k\.?a\b\.?", "also known as", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\b([A-Za-z0-9][\w-]*)\.(com|org|net|edu|gov|io|co|uk)\b",
        lambda m: f"{m.group(1)} dot {m.group(2)}",
        text,
        flags=re.IGNORECASE,
    )
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


def _select_voice_prompt() -> Path:
    if VOICE_PROMPT_CLEAN.exists() and VOICE_PROMPT_CLEAN.stat().st_size > 1000:
        return VOICE_PROMPT_CLEAN
    if VOICE_PROMPT_RAW.exists():
        return VOICE_PROMPT_RAW
    raise FileNotFoundError(
        f"Missing JARVIS voice clone prompt. Expected {VOICE_PROMPT_CLEAN} or {VOICE_PROMPT_RAW}"
    )


def _ensure_model_loaded():
    """Load Pocket TTS with voice-cloning weights + cleaned JARVIS prompt."""
    global _model, _voice_state
    if _model is not None and _voice_state is not None:
        return
    with _model_lock:
        if _model is not None and _voice_state is not None:
            return

        prompt_path = _select_voice_prompt()

        # Lower temp + more LSD steps = less hiss/artifacts on clone voices
        _model = TTSModel.load_model(
            temp=_LOAD_TEMP,
            lsd_decode_steps=_LOAD_LSD_STEPS,
            noise_clamp=_LOAD_NOISE_CLAMP,
            eos_threshold=_LOAD_EOS_THRESHOLD,
        )
        if not getattr(_model, "has_voice_cloning", True):
            raise RuntimeError(
                "Pocket TTS loaded WITHOUT voice-cloning weights. "
                "Accept terms at https://huggingface.co/kyutai/pocket-tts and run "
                "`hf auth login`, then restart JARVIS."
            )
        try:
            # truncate=True guards against overly long prompts
            _voice_state = _model.get_state_for_audio_prompt(prompt_path, truncate=True)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to condition TTS on JARVIS clone wav ({prompt_path.name}): {exc}"
            ) from exc
        print(
            f"[TTS] Using JARVIS cloned voice: {prompt_path.name} "
            f"(temp={_LOAD_TEMP}, lsd={_LOAD_LSD_STEPS}, noise_clamp={_LOAD_NOISE_CLAMP})"
        )


def warm_up_tts():
    """Load model, resolve playback rate, and prime inference."""
    try:
        _ensure_model_loaded()
        rate = _resolve_playback_rate(_model.sample_rate)
        # One short full generate (not stream) to warm caches cleanly
        _ = _model.generate_audio(
            model_state=_voice_state,
            text_to_generate="Ready.",
            frames_after_eos=1,
            copy_state=True,
        )
        print(f"[TTS] Pocket TTS warmed ({rate}Hz playback, quality mode).")
    except Exception as exc:
        print(f"[TTS Warmup Error] {exc}")


def _chunk_to_samples(chunk) -> np.ndarray:
    if hasattr(chunk, "detach"):
        chunk = chunk.detach().cpu().numpy()
    return np.asarray(chunk, dtype=np.float32).reshape(-1)


def _postprocess_audio(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Reduce hiss/clicks: fade, soft high-pass, soft limit, normalize."""
    if samples.size == 0:
        return samples.astype(np.float32)

    x = samples.astype(np.float64).copy()

    # DC remove
    x = x - np.mean(x)

    # Mild one-pole high-pass ~70 Hz (rumble / clone low-end mud)
    # y[n] = a*(y[n-1] + x[n] - x[n-1])
    rc = 1.0 / (2.0 * np.pi * 70.0)
    dt = 1.0 / float(sample_rate)
    a = rc / (rc + dt)
    y = np.empty_like(x)
    y[0] = x[0]
    for i in range(1, len(x)):
        y[i] = a * (y[i - 1] + x[i] - x[i - 1])
    x = y

    # Soft high-shelf attenuate for hiss (simple 1-pole lowpass blend)
    # blend 85% original + 15% lowpassed (~9 kHz)
    alpha = np.exp(-2.0 * np.pi * 9000.0 / sample_rate)
    lp = np.empty_like(x)
    lp[0] = x[0]
    for i in range(1, len(x)):
        lp[i] = alpha * lp[i - 1] + (1 - alpha) * x[i]
    x = 0.82 * x + 0.18 * lp

    # Soft limiter (tanh) then peak normalize to -1.5 dB
    x = np.tanh(x * 1.15)
    peak = float(np.max(np.abs(x))) + 1e-9
    target = 10 ** (-1.5 / 20.0)  # ~0.84
    x = x / peak * target

    # Fade in/out 12 ms to kill edge clicks
    fade = max(1, int(sample_rate * 0.012))
    if len(x) > 2 * fade:
        env = np.ones(len(x), dtype=np.float64)
        env[:fade] = np.linspace(0.0, 1.0, fade)
        env[-fade:] = np.linspace(1.0, 0.0, fade)
        x = x * env

    return np.clip(x, -1.0, 1.0).astype(np.float32)


def _synthesize_clean(clean_text: str) -> np.ndarray:
    """Full-buffer synthesis (clearer than tiny stream writes on Windows)."""
    # frames_after_eos: a bit more tail so words don't cut off harshly
    word_count = max(1, len(clean_text.split()))
    frames_after = 3 if word_count <= 6 else 2

    audio = _model.generate_audio(
        model_state=_voice_state,
        text_to_generate=clean_text,
        frames_after_eos=frames_after,
        copy_state=True,
    )
    samples = _chunk_to_samples(audio)
    return _postprocess_audio(samples, _model.sample_rate)


def _play_samples(samples: np.ndarray, playback_rate: int, model_rate: int) -> None:
    if samples.size == 0:
        return
    if playback_rate != model_rate:
        samples = _resample_once(samples, model_rate, playback_rate)

    # High latency = larger host buffer = fewer underruns/crackle
    try:
        sd.play(samples, samplerate=playback_rate, blocking=False, latency="high")
    except TypeError:
        sd.play(samples, samplerate=playback_rate, blocking=False)

    # Wait while allowing stop_speech()
    duration = len(samples) / float(playback_rate)
    step = 0.05
    waited = 0.0
    while waited < duration + 0.05:
        if _stop_event.is_set():
            try:
                sd.stop()
            except Exception:
                pass
            break
        sd.sleep(int(step * 1000))
        waited += step


def _pause_local_music():
    try:
        from executor.music_services import pause_local_music

        pause_local_music()
    except Exception:
        pass


def _restore_local_music():
    try:
        from executor.music_services import resume_after_speech

        resume_after_speech()
    except Exception:
        pass


def speak(text: str):
    """Synthesize with clone voice and play cleanly (blocking until done/stop)."""
    global _is_speaking

    clean_text = clean_text_for_speech(text)
    if len(clean_text) < 1:
        return

    try:
        _ensure_model_loaded()
    except Exception as exc:
        print(f"[TTS Load Error] {exc}")
        return

    with _playback_lock:
        _stop_event.clear()
        _is_speaking = True
        _pause_local_music()

        try:
            playback_rate = _resolve_playback_rate(_model.sample_rate)
            print(
                f"[TTS] Quality synthesize+play ({playback_rate}Hz, "
                f"chars={len(clean_text)})"
            )
            samples = _synthesize_clean(clean_text)
            if _stop_event.is_set():
                return
            _play_samples(samples, playback_rate, _model.sample_rate)
        except Exception as exc:
            print(f"[TTS Audio Error] {exc}")
            # Fallback: stream path if full generate fails
            try:
                if not _stop_event.is_set():
                    _stream_fallback(clean_text, _resolve_playback_rate(_model.sample_rate))
            except Exception as exc2:
                print(f"[TTS Fallback Error] {exc2}")
        finally:
            _restore_local_music()
            _is_speaking = False
            _stop_event.clear()


def _stream_fallback(clean_text: str, playback_rate: int) -> None:
    """Buffered stream fallback — still post-processes each write block."""
    model_rate = _model.sample_rate
    direct = playback_rate == model_rate
    buf: list[np.ndarray] = []
    buf_samples = 0
    min_buf = int(model_rate * 0.25)  # 250 ms before first write

    with sd.OutputStream(
        samplerate=playback_rate,
        channels=1,
        dtype="float32",
        blocksize=2048,
        latency="high",
    ) as stream:
        for chunk in _model.generate_audio_stream(
            model_state=_voice_state,
            text_to_generate=clean_text,
            copy_state=True,
            frames_after_eos=2,
        ):
            if _stop_event.is_set():
                break
            samples = _chunk_to_samples(chunk)
            if samples.size == 0:
                continue
            buf.append(samples)
            buf_samples += len(samples)
            if buf_samples < min_buf:
                continue
            block = np.concatenate(buf)
            buf.clear()
            buf_samples = 0
            block = _postprocess_audio(block, model_rate)
            if not direct:
                block = _resample_once(block, model_rate, playback_rate)
            stream.write(block.reshape(-1, 1))
        if buf and not _stop_event.is_set():
            block = _postprocess_audio(np.concatenate(buf), model_rate)
            if not direct:
                block = _resample_once(block, model_rate, playback_rate)
            stream.write(block.reshape(-1, 1))


def stop_speech():
    global _is_speaking

    _stop_event.set()
    _is_speaking = False
    try:
        sd.stop()
    except Exception as exc:
        print(f"[TTS Stop Error] {exc}")


def is_speaking() -> bool:
    return _is_speaking
