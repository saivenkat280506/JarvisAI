"""
pocket_tts.py — JARVIS voice (Pocket TTS only)
==============================================
Clones tts/voices/jarvis_voice_clean.wav (or 'jarvis voice.wav').

No XTTS, Edge, or macOS `say`. If Pocket cloning weights are missing,
speech is skipped rather than substituting another engine.

Playback is full-utterance (not streamed) and refused when the buffer
looks like noise, NaN, or full-scale hash — those are what blow speakers.
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

try:
    from pocket_tts import TTSModel
except ImportError:
    TTSModel = None

VOICES_DIR = Path(__file__).with_name("voices")
VOICE_PROMPT_CLEAN = VOICES_DIR / "jarvis_voice_clean.wav"
VOICE_PROMPT_RAW = VOICES_DIR / "jarvis voice.wav"
# Official gated clone checkpoint from pocket-tts english.yaml (do not use the
# public pocket-tts-without-voice-cloning fallback — that audio is unsafe).
CLONE_WEIGHTS = (
    "hf://kyutai/pocket-tts/languages/english/model.safetensors"
    "@39592ff23c9ef80098bb74895d104c26275fe2c9"
)

_model = None
_voice_state = None
_playback_rate: int | None = None
_model_lock = threading.Lock()
_playback_lock = threading.Lock()
_stop_event = threading.Event()
_is_speaking = False
_pocket_clone_ok: bool | None = None

# Cleaner than LSD=1 (that setting was the hiss source)
_LOAD_TEMP = 0.30
_LOAD_LSD_STEPS = 4
_LOAD_NOISE_CLAMP = 1.5
_LOAD_EOS_THRESHOLD = -3.0

_MAX_SENTENCE_CHARS = 110
_PEAK_TARGET = 10 ** (-6.0 / 20.0)  # -6 dBFS
_MAX_BOOST = 10 ** (6.0 / 20.0)


def _select_voice_prompt() -> Path:
    if VOICE_PROMPT_CLEAN.exists() and VOICE_PROMPT_CLEAN.stat().st_size > 1000:
        return VOICE_PROMPT_CLEAN
    if VOICE_PROMPT_RAW.exists() and VOICE_PROMPT_RAW.stat().st_size > 1000:
        return VOICE_PROMPT_RAW
    raise FileNotFoundError(
        f"Missing JARVIS voice clone prompt. Expected {VOICE_PROMPT_CLEAN} or {VOICE_PROMPT_RAW}"
    )


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
    try:
        from brain.jarvis_style import shape_spoken_text
        text = shape_spoken_text(text)
    except Exception:
        pass
    return text.strip()


def _hf_token() -> str:
    token = (
        os.environ.get("HF_TOKEN")
        or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or ""
    ).strip()
    if token:
        return token
    try:
        from config import settings

        token = (getattr(settings, "HF_TOKEN", "") or "").strip()
    except Exception:
        token = ""
    return token


def _require_cloning_weights() -> None:
    from pocket_tts.utils.utils import download_if_necessary

    token = _hf_token()
    if not token:
        raise RuntimeError(
            "HF_TOKEN is not set. Put a Hugging Face token with access to "
            "kyutai/pocket-tts in backend/.env"
        )
    os.environ["HF_TOKEN"] = token
    os.environ["HUGGING_FACE_HUB_TOKEN"] = token
    try:
        from huggingface_hub import login

        login(token=token, add_to_git_credential=False)
    except Exception as exc:
        print(f"[TTS] Hugging Face login warning: {type(exc).__name__}")
    try:
        download_if_necessary(CLONE_WEIGHTS)
    except Exception as exc:
        raise RuntimeError(
            "Could not download official Pocket TTS cloning weights from "
            "kyutai/pocket-tts. Accept the model terms on Hugging Face, then "
            f"retry. ({type(exc).__name__})"
        ) from exc


def _ensure_model_loaded() -> None:
    global _model, _voice_state, _pocket_clone_ok
    if _pocket_clone_ok is False:
        raise RuntimeError("Pocket official cloning is unavailable")
    if _model is not None and _voice_state is not None:
        return
    if TTSModel is None:
        _pocket_clone_ok = False
        raise RuntimeError("pocket_tts is not installed")
    with _model_lock:
        if _pocket_clone_ok is False:
            raise RuntimeError("Pocket official cloning is unavailable")
        if _model is not None and _voice_state is not None:
            return
        try:
            prompt_path = _select_voice_prompt()
            _require_cloning_weights()
            model = TTSModel.load_model(
                temp=_LOAD_TEMP,
                sampler_decode_steps=_LOAD_LSD_STEPS,
                noise_clamp=_LOAD_NOISE_CLAMP,
                eos_threshold=_LOAD_EOS_THRESHOLD,
            )
            if not getattr(model, "has_voice_cloning", True):
                raise RuntimeError("Pocket TTS loaded without official cloning weights")
            voice_state = model.get_state_for_audio_prompt(prompt_path, truncate=True)
            _model = model
            _voice_state = voice_state
            _pocket_clone_ok = True
            print(
                f"[TTS] Pocket TTS official JARVIS clone: {prompt_path.name} "
                f"(temp={_LOAD_TEMP}, steps={_LOAD_LSD_STEPS})"
            )
        except Exception:
            _pocket_clone_ok = False
            _model = None
            _voice_state = None
            raise


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


def _postprocess(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    if samples.size == 0:
        return samples.astype(np.float32)
    x = samples.astype(np.float64).copy()
    x -= np.mean(x)
    hp_win = max(3, int(sample_rate / 70.0))
    x = x - _moving_average(x, hp_win)
    lp_win = max(3, int(sample_rate / 9000.0))
    lp = _moving_average(x, lp_win)
    x = 0.90 * x + 0.10 * lp
    peak = float(np.max(np.abs(x))) + 1e-9
    gain = min(_PEAK_TARGET / peak, _MAX_BOOST)
    x *= gain
    fade = max(1, int(sample_rate * 0.012))
    if len(x) > 2 * fade:
        x[:fade] *= np.linspace(0.0, 1.0, fade)
        x[-fade:] *= np.linspace(1.0, 0.0, fade)
    return np.clip(x, -1.0, 1.0).astype(np.float32)


def _trim_to_speech(samples: np.ndarray, sample_rate: int, word_count: int) -> np.ndarray:
    """Cut trailing hum/silence when the local clone fails to emit EOS."""
    if samples.size == 0:
        return samples
    win = max(8, int(sample_rate * 0.04))
    sq = samples.astype(np.float64) ** 2
    kernel = np.ones(win, dtype=np.float64) / win
    rms = np.sqrt(np.convolve(sq, kernel, mode="same"))
    thresh = max(0.012, 0.07 * float(np.max(rms)))
    voiced = np.where(rms > thresh)[0]
    if voiced.size == 0:
        return samples[:0]
    start = max(0, int(voiced[0] - sample_rate * 0.02))
    end = min(samples.size, int(voiced[-1] + sample_rate * 0.18))
    trimmed = samples[start:end]
    max_sec = min(12.0, max(1.1, word_count * 0.45 + 1.0))
    max_n = int(max_sec * sample_rate)
    if trimmed.size > max_n:
        trimmed = trimmed[:max_n]
        fade = max(1, int(sample_rate * 0.02))
        if trimmed.size > fade:
            trimmed = trimmed.copy()
            trimmed[-fade:] *= np.linspace(1.0, 0.0, fade).astype(trimmed.dtype)
    return trimmed


def audio_is_safe(samples: np.ndarray, sample_rate: int) -> tuple[bool, str]:
    """Return whether a buffer is safe to send to a speaker."""
    if samples is None or samples.size == 0:
        return False, "empty"
    if samples.size < max(32, sample_rate // 50):
        return False, "too-short"
    if not np.isfinite(samples).all():
        return False, "nan/inf"
    x = samples.astype(np.float64).reshape(-1)
    peak = float(np.max(np.abs(x)))
    rms = float(np.sqrt(np.mean(x * x)))
    if peak > 1.001:
        return False, f"overrange peak={peak:.3f}"
    if peak < 0.02 or rms < 0.004:
        return False, f"too-quiet peak={peak:.4f} rms={rms:.4f}"
    if peak > 0.98 and rms > 0.35:
        return False, f"noise-like peak={peak:.3f} rms={rms:.3f}"
    clip_frac = float(np.mean(np.abs(x) > 0.99))
    if clip_frac > 0.01:
        return False, f"clipped {clip_frac:.1%}"
    # Spectral flatness in the speech band. ~1 is white noise.
    spec = np.abs(np.fft.rfft(x * np.hanning(x.size)))
    spec = np.maximum(spec, 1e-12)
    freqs = np.fft.rfftfreq(x.size, 1.0 / sample_rate)
    band = spec[(freqs >= 200) & (freqs <= 4000)]
    if band.size:
        flatness = float(np.exp(np.mean(np.log(band))) / np.mean(band))
        if flatness > 0.55:
            return False, f"noise-flatness={flatness:.2f}"
    else:
        flatness = 0.0
    return True, f"ok peak={peak:.3f} rms={rms:.3f} dur={len(x) / sample_rate:.2f}s flat={flatness:.2f}"


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


def synthesize(text: str) -> tuple[np.ndarray, int]:
    """Generate float32 mono at the model rate. Does not play audio."""
    _ensure_model_loaded()
    clean_text = clean_text_for_speech(text)
    if not clean_text:
        return np.zeros(0, dtype=np.float32), int(_model.sample_rate)
    word_count = max(1, len(clean_text.split()))
    frames_after = 3 if word_count <= 8 else 2
    # 12.5 Hz latents; enough headroom so short lines are not cut at 4s.
    max_tokens = min(240, max(90, word_count * 10 + 50))
    chunks: list[np.ndarray] = []
    for chunk in _model.generate_audio_stream(
        model_state=_voice_state,
        text_to_generate=clean_text,
        copy_state=True,
        frames_after_eos=frames_after,
        max_tokens=max_tokens,
    ):
        if _stop_event.is_set():
            break
        samples = _chunk_to_samples(chunk)
        if samples.size:
            chunks.append(samples)
    if not chunks:
        return np.zeros(0, dtype=np.float32), int(_model.sample_rate)
    raw = np.concatenate(chunks)
    rate = int(_model.sample_rate)
    polished = _postprocess(raw, rate)
    polished = _trim_to_speech(polished, rate, word_count)
    return polished, rate


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


def _play_samples(samples: np.ndarray, model_rate: int) -> None:
    ok, reason = audio_is_safe(samples, model_rate)
    if not ok:
        print(f"[TTS] Refusing playback ({reason}) — protecting speakers.")
        return
    playback_rate = _resolve_playback_rate(model_rate)
    if playback_rate != model_rate:
        samples = _resample_once(samples, model_rate, playback_rate)
        ok, reason = audio_is_safe(samples, playback_rate)
        if not ok:
            print(f"[TTS] Refusing resampled playback ({reason}) — protecting speakers.")
            return
    print(f"[TTS] Pocket play {reason} @ {playback_rate}Hz")
    # Continuous stream avoids sd.play underruns between buffers.
    idx = 0
    n = int(samples.size)
    fade = max(1, int(playback_rate * 0.008))
    if n > 2 * fade:
        samples = samples.copy()
        samples[:fade] *= np.linspace(0.0, 1.0, fade, dtype=np.float32)
        samples[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)

    def _cb(outdata, frames, _time, status):
        nonlocal idx
        if status:
            print(f"[TTS] stream status: {status}")
        if _stop_event.is_set() or idx >= n:
            outdata.fill(0)
            raise sd.CallbackStop
        end = min(n, idx + frames)
        chunk = samples[idx:end]
        outdata[: len(chunk), 0] = chunk
        if len(chunk) < frames:
            outdata[len(chunk) :, 0] = 0
        idx = end
        if idx >= n:
            raise sd.CallbackStop

    try:
        with sd.OutputStream(
            samplerate=playback_rate,
            channels=1,
            dtype="float32",
            callback=_cb,
            blocksize=2048,
            latency="high",
        ):
            while idx < n and not _stop_event.is_set():
                sd.sleep(20)
    except sd.CallbackStop:
        pass
    except Exception as exc:
        print(f"[TTS] OutputStream failed ({exc}); falling back to sd.play")
        try:
            sd.play(samples, samplerate=playback_rate, blocking=True)
        except Exception as play_exc:
            print(f"[TTS] sd.play failed: {play_exc}")


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


def warm_up_tts():
    """Load official Pocket clone of jarvis_voice_clean.wav. Does not play."""
    try:
        t0 = time.perf_counter()
        _ensure_model_loaded()
        samples, rate = synthesize("Systems online.")
        ok, reason = audio_is_safe(samples, rate)
        ms = (time.perf_counter() - t0) * 1000
        print(f"[TTS] Pocket warmup safe={ok} {reason} in {ms:.0f}ms.")
        if not ok:
            raise RuntimeError(f"Pocket warmup audio failed safety check: {reason}")
    except Exception as exc:
        print(f"[TTS Warmup Error] {exc}")


def speak(text: str):
    """Speak as JARVIS via official Pocket TTS cloning. Skips audio if unsafe."""
    global _is_speaking

    clean_text = clean_text_for_speech(text)
    if len(clean_text) < 1:
        return

    sentences = _split_for_progressive(clean_text)
    with _playback_lock:
        _stop_event.clear()
        _is_speaking = True
        _duck_local_music()
        t0 = time.perf_counter()
        try:
            _ensure_model_loaded()
            print(
                f"[TTS] Pocket TTS official JARVIS clone speak chars={len(clean_text)} "
                f"parts={len(sentences)}"
            )
            next_audio: tuple[np.ndarray, int] | None = None
            for i, sentence in enumerate(sentences):
                if _stop_event.is_set():
                    break
                if next_audio is not None:
                    samples, rate = next_audio
                    next_audio = None
                else:
                    samples, rate = synthesize(sentence)
                ok, reason = audio_is_safe(samples, rate)
                if not ok:
                    print(f"[TTS] Refusing playback ({reason}) — protecting speakers.")
                    continue
                nxt = sentences[i + 1] if i + 1 < len(sentences) else None
                gen_box: dict = {}
                gen_thread = None
                if nxt and not _stop_event.is_set():
                    def _gen(line=nxt):
                        try:
                            gen_box["audio"] = synthesize(line)
                        except Exception as exc:
                            gen_box["err"] = exc

                    gen_thread = threading.Thread(target=_gen, daemon=True)
                    gen_thread.start()
                _play_samples(samples, rate)
                if gen_thread is not None:
                    gen_thread.join()
                    if "err" in gen_box:
                        print(f"[TTS] Next-line generate failed: {gen_box['err']}")
                    else:
                        next_audio = gen_box.get("audio")
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
