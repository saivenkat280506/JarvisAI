"""
pocket_tts.py — JARVIS voice clone (Pocket TTS)
================================================
Clean + snappy path:
  - Quality decode (temp/LSD restored — LSD 1 was the hiss source)
  - Stream playback with a stable ~200 ms first buffer (fast start, less crackle)
  - Fixed-gain post-filter (no per-chunk peak-normalize pumping)
  - Progressive sentences so long replies start on the first line
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
from pocket_tts import TTSModel

VOICES_DIR = Path(__file__).with_name("voices")
VOICE_PROMPT_CLEAN = VOICES_DIR / "jarvis_voice_clean.wav"
VOICE_PROMPT_RAW = VOICES_DIR / "jarvis voice.wav"

_model = None
_voice_state = None
_playback_rate: int | None = None
_model_lock = threading.Lock()
_playback_lock = threading.Lock()
_stop_event = threading.Event()
_is_speaking = False

# Quality knobs — LSD 1 / high temp caused the audible noise
_LOAD_TEMP = 0.45
_LOAD_LSD_STEPS = 3
_LOAD_NOISE_CLAMP = 1.8
_LOAD_EOS_THRESHOLD = -3.0

# Stream: larger first buffer = smoother, still much faster than full-utterance wait
_FIRST_BUFFER_SEC = 0.20
_CHUNK_BUFFER_SEC = 0.08
_MAX_SENTENCE_CHARS = 110
# Fixed output gain (avoid re-normalizing every tiny block → pumping/hiss)
_OUTPUT_GAIN = 0.78


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
    global _model, _voice_state
    if _model is not None and _voice_state is not None:
        return
    with _model_lock:
        if _model is not None and _voice_state is not None:
            return

        prompt_path = _select_voice_prompt()
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
            _voice_state = _model.get_state_for_audio_prompt(prompt_path, truncate=True)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to condition TTS on JARVIS clone wav ({prompt_path.name}): {exc}"
            ) from exc
        print(
            f"[TTS] Using JARVIS cloned voice: {prompt_path.name} "
            f"(temp={_LOAD_TEMP}, lsd={_LOAD_LSD_STEPS}, noise_clamp={_LOAD_NOISE_CLAMP}, clean stream)"
        )


def warm_up_tts():
    try:
        t0 = time.perf_counter()
        _ensure_model_loaded()
        rate = _resolve_playback_rate(_model.sample_rate)
        n = 0
        for chunk in _model.generate_audio_stream(
            model_state=_voice_state,
            text_to_generate="Ready.",
            copy_state=True,
            frames_after_eos=1,
        ):
            n += 1
            _ = _chunk_to_samples(chunk)
            if n >= 4:
                break
        print(
            f"[TTS] Pocket TTS warmed ({rate}Hz, clean stream) "
            f"in {(time.perf_counter() - t0) * 1000:.0f}ms."
        )
    except Exception as exc:
        print(f"[TTS Warmup Error] {exc}")


def _chunk_to_samples(chunk) -> np.ndarray:
    if hasattr(chunk, "detach"):
        chunk = chunk.detach().cpu().numpy()
    return np.asarray(chunk, dtype=np.float32).reshape(-1)


def _moving_average(x: np.ndarray, win: int) -> np.ndarray:
    if win <= 1 or x.size == 0:
        return x
    win = int(min(win, max(1, x.size // 2)))
    c = np.cumsum(np.insert(x.astype(np.float64), 0, 0.0))
    ma = (c[win:] - c[:-win]) / float(win)
    pad_l = win // 2
    pad_r = x.size - ma.size - pad_l
    if pad_r < 0:
        ma = ma[: x.size]
        pad_r = 0
        pad_l = x.size - ma.size
    return np.pad(ma, (max(0, pad_l), max(0, pad_r)), mode="edge")[: x.size]


def _postprocess_block(
    samples: np.ndarray,
    sample_rate: int,
    *,
    fade_in: bool = False,
    fade_out: bool = False,
    full_clean: bool = False,
) -> np.ndarray:
    """
    Clean stream/full audio without per-block peak normalize (that caused pumping).
    full_clean: stronger filter for complete buffers / last block.
    """
    if samples.size == 0:
        return samples.astype(np.float32)

    x = samples.astype(np.float64).copy()
    x -= np.mean(x)

    if full_clean or samples.size > sample_rate // 4:
        # Rumble cut
        hp_win = max(3, int(sample_rate / 70.0))
        x = x - _moving_average(x, hp_win)
        # Mild de-hiss
        lp_win = max(3, int(sample_rate / 8500.0))
        lp = _moving_average(x, lp_win)
        x = 0.84 * x + 0.16 * lp

    # Soft limit + fixed gain (stable loudness across stream blocks)
    x = np.tanh(x * 1.08) * _OUTPUT_GAIN

    fade = max(1, int(sample_rate * 0.008))
    if fade_in and len(x) > fade:
        x[:fade] *= np.linspace(0.0, 1.0, fade)
    if fade_out and len(x) > fade:
        x[-fade:] *= np.linspace(1.0, 0.0, fade)

    return np.clip(x, -1.0, 1.0).astype(np.float32)


def _postprocess_full(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Full-utterance polish (fallback path)."""
    if samples.size == 0:
        return samples.astype(np.float32)
    x = samples.astype(np.float64).copy()
    x -= np.mean(x)
    hp_win = max(3, int(sample_rate / 70.0))
    x = x - _moving_average(x, hp_win)
    lp_win = max(3, int(sample_rate / 8500.0))
    lp = _moving_average(x, lp_win)
    x = 0.82 * x + 0.18 * lp
    x = np.tanh(x * 1.12)
    peak = float(np.max(np.abs(x))) + 1e-9
    x = x / peak * (10 ** (-1.5 / 20.0))
    fade = max(1, int(sample_rate * 0.010))
    if len(x) > 2 * fade:
        env = np.ones(len(x), dtype=np.float64)
        env[:fade] = np.linspace(0.0, 1.0, fade)
        env[-fade:] = np.linspace(1.0, 0.0, fade)
        x *= env
    return np.clip(x, -1.0, 1.0).astype(np.float32)


def _split_for_progressive(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= _MAX_SENTENCE_CHARS:
        return [text]

    parts = re.split(r"(?<=[.!?])\s+", text)
    parts = [p.strip() for p in parts if p and p.strip()]
    if len(parts) <= 1:
        parts = re.split(r"(?<=[,;:])\s+", text)
        parts = [p.strip() for p in parts if p and p.strip()]
    if not parts:
        return [text]

    merged: list[str] = []
    buf = ""
    for p in parts:
        if not buf:
            buf = p
        elif len(buf) + 1 + len(p) <= _MAX_SENTENCE_CHARS:
            buf = f"{buf} {p}"
        else:
            merged.append(buf)
            buf = p
    if buf:
        merged.append(buf)
    return merged or [text]


def _stream_and_play(clean_text: str, playback_rate: int) -> float:
    """Stream with quality decode + stable gain. Returns TTFA seconds."""
    model_rate = _model.sample_rate
    direct = playback_rate == model_rate
    first_buf = int(model_rate * _FIRST_BUFFER_SEC)
    chunk_buf = int(model_rate * _CHUNK_BUFFER_SEC)
    word_count = max(1, len(clean_text.split()))
    frames_after = 3 if word_count <= 8 else 2

    buf: list[np.ndarray] = []
    buf_samples = 0
    started = False
    t_start = time.perf_counter()
    t_first = None

    # High host latency reduces underrun crackle on Windows
    stream_kwargs = dict(
        samplerate=playback_rate,
        channels=1,
        dtype="float32",
        blocksize=2048,
        latency="high",
    )
    try:
        out_stream = sd.OutputStream(**stream_kwargs)
    except Exception:
        stream_kwargs.pop("latency", None)
        out_stream = sd.OutputStream(**stream_kwargs)

    with out_stream as stream:
        for chunk in _model.generate_audio_stream(
            model_state=_voice_state,
            text_to_generate=clean_text,
            copy_state=True,
            frames_after_eos=frames_after,
        ):
            if _stop_event.is_set():
                break
            samples = _chunk_to_samples(chunk)
            if samples.size == 0:
                continue
            buf.append(samples)
            buf_samples += len(samples)

            need = first_buf if not started else chunk_buf
            if buf_samples < need:
                continue

            block = np.concatenate(buf)
            buf.clear()
            buf_samples = 0
            block = _postprocess_block(
                block,
                model_rate,
                fade_in=not started,
                fade_out=False,
                full_clean=True,
            )
            if not direct:
                block = _resample_once(block, model_rate, playback_rate)
            if _stop_event.is_set():
                break
            stream.write(block.reshape(-1, 1))
            if not started:
                started = True
                t_first = time.perf_counter() - t_start

        if buf and not _stop_event.is_set():
            block = _postprocess_block(
                np.concatenate(buf),
                model_rate,
                fade_in=not started,
                fade_out=True,
                full_clean=True,
            )
            if not direct:
                block = _resample_once(block, model_rate, playback_rate)
            stream.write(block.reshape(-1, 1))
            if not started:
                t_first = time.perf_counter() - t_start

    return float(t_first if t_first is not None else (time.perf_counter() - t_start))


def _synthesize_full_fallback(clean_text: str) -> np.ndarray:
    word_count = max(1, len(clean_text.split()))
    frames_after = 3 if word_count <= 6 else 2
    audio = _model.generate_audio(
        model_state=_voice_state,
        text_to_generate=clean_text,
        frames_after_eos=frames_after,
        copy_state=True,
    )
    return _postprocess_full(_chunk_to_samples(audio), _model.sample_rate)


def _play_samples(samples: np.ndarray, playback_rate: int, model_rate: int) -> None:
    if samples.size == 0:
        return
    if playback_rate != model_rate:
        samples = _resample_once(samples, model_rate, playback_rate)
    try:
        sd.play(samples, samplerate=playback_rate, blocking=False, latency="high")
    except TypeError:
        sd.play(samples, samplerate=playback_rate, blocking=False)

    duration = len(samples) / float(playback_rate)
    step = 0.04
    waited = 0.0
    while waited < duration + 0.06:
        if _stop_event.is_set():
            try:
                sd.stop()
            except Exception:
                pass
            break
        sd.sleep(int(step * 1000))
        waited += step


def _duck_local_music():
    """Keep garage music playing under speech — only lower volume, never stop."""
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


def _speak_unit(clean_text: str, playback_rate: int) -> None:
    if not clean_text or _stop_event.is_set():
        return
    try:
        ttfa = _stream_and_play(clean_text, playback_rate)
        print(f"[TTS] Stream unit chars={len(clean_text)} first_audio={ttfa * 1000:.0f}ms")
    except Exception as exc:
        print(f"[TTS] Stream failed ({exc}); full-buffer fallback")
        try:
            samples = _synthesize_full_fallback(clean_text)
            if not _stop_event.is_set():
                _play_samples(samples, playback_rate, _model.sample_rate)
        except Exception as exc2:
            print(f"[TTS] Fallback failed: {exc2}")


def speak(text: str):
    """Clean clone voice with progressive sentences + stable stream playback."""
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
        _duck_local_music()
        t0 = time.perf_counter()

        try:
            playback_rate = _resolve_playback_rate(_model.sample_rate)
            units = _split_for_progressive(clean_text)
            print(
                f"[TTS] Clean stream speak ({playback_rate}Hz, "
                f"chars={len(clean_text)}, units={len(units)})"
            )
            for unit in units:
                if _stop_event.is_set():
                    break
                _speak_unit(unit, playback_rate)
            print(f"[TTS] Speak finished in {(time.perf_counter() - t0) * 1000:.0f}ms wall")
        except Exception as exc:
            print(f"[TTS Audio Error] {exc}")
        finally:
            _restore_local_music()
            _is_speaking = False
            _stop_event.clear()


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
