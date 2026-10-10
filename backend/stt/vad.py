"""
vad.py — Industrial Real-Time Voice Activity Detection (VAD) Pipeline for JARVIS
================================================================================
A 5-Stage Systems Audio Pipeline:

1. Audio Input Stage:
   - High-priority sounddevice InputStream capturing 16kHz, 16-bit mono PCM in
     512-sample frames (~32ms per frame).
   - Rolling pre-speech circular ring buffer (default 300ms) ensuring initial plosive
     consonants (/p/, /t/, /k/, /d/, /j/) are preserved before trigger threshold.

2. Energy Estimation Module:
   - Real-time RMS (Root-Mean-Square) amplitude, Peak, and logarithmic dBFS volume.
   - Dynamic adaptive background noise floor tracking using recursive smoothing.
   - Signal-to-Noise Ratio (SNR in dB) estimation above ambient noise.

3. Spectral Analysis Component:
   - Real Fast Fourier Transform (RFFT) frequency decomposition.
   - Voice Formant Band Energy Ratio (300Hz - 3400Hz vs full spectrum) to isolate
     human vocal tract resonance from sub-bass HVAC hum (<200Hz) and high-frequency hiss.
   - Zero-Crossing Rate (ZCR) and Spectral Centroid measurement.

4. Classification Logic & Multi-Feature Fusion:
   - Multi-feature fusion combining Energy (SNR), Spectral Voice Ratio, and Silero VAD neural probability.
   - Dual-threshold Schmitt trigger hysteresis (attack onset threshold vs sustain threshold)
     to eliminate boundary fluttering.
   - 3-State Finite State Machine: IDLE/LISTENING -> COLLECTING_SPEECH -> PROCESSING.

5. Tunable Thresholds & Timing Management:
   - Tunable sensitivity, noise floors, attack/sustain probabilities, and hangover duration.
   - Silence hangover duration (default >= 600ms) prevents premature cutoffs during natural pauses.
   - Minimum speech duration filter (default >= 350ms) rejects coughs, clicks, and door slams.
   - Low-latency barge-in engine interrupting Pocket TTS audio output on detected speech.
"""

from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
import math
import os
import queue
import threading
import time
from typing import Callable, Optional, Tuple

import numpy as np
import sounddevice as sd
import torch


# ── Standard Audio Constants ──────────────────────────────────────────────────

SAMPLE_RATE: int = 16000
CHUNK_SAMPLES: int = 512


# ── Tunable Configuration Dataclass ───────────────────────────────────────────

@dataclass
class VADConfig:
    """
    Tunable parameters for sensitivity, energy, spectral analysis, and timing.
    Adjustable at runtime to adapt to different microphones and environments.
    """
    # Audio capture
    sample_rate: int = 16000
    chunk_samples: int = 512  # 512 samples at 16kHz = 32ms

    # Energy Estimation
    min_energy_rms: float = 0.008             # Minimum absolute RMS to consider sound
    min_snr_db: float = 3.5                   # Minimum SNR (dB) above dynamic noise floor
    noise_floor_alpha: float = 0.96           # Recursive smoothing factor for ambient noise tracking
    initial_noise_floor: float = 0.004        # Default baseline noise floor estimate

    # Spectral Analysis
    speech_band_low_hz: float = 300.0         # Lower boundary of human speech formants
    speech_band_high_hz: float = 3400.0       # Upper boundary of human speech formants
    min_speech_band_ratio: float = 0.35       # Fraction of spectral energy in speech band
    min_zcr: float = 0.015                    # Minimum Zero-Crossing Rate (rejects pure DC/sub-bass)
    max_zcr: float = 0.65                     # Maximum Zero-Crossing Rate (rejects pure high-frequency hiss)

    # Classification & Neural Thresholds (Hysteresis)
    speech_onset_prob: float = 0.60           # Threshold to initiate speech state (Attack)
    speech_sustain_prob: float = 0.40         # Lower threshold to maintain speech state (Hysteresis)
    barge_in_prob: float = 0.70               # Threshold to trigger barge-in while TTS is outputting
    barge_in_consecutive_frames: int = 2      # Consecutive high-confidence frames to interrupt TTS

    # Timing & State Machine
    pre_speech_ms: int = 300                  # Pre-speech circular ring buffer duration
    silence_timeout_ms: int = 600             # Hangover silence before finalizing speech
    min_speech_duration_ms: int = 350         # Discard utterances shorter than this (clicks/coughs)
    max_speech_duration_s: float = 30.0       # Safety cutoff for runaway speech buffers

    # Whisper STT
    whisper_model: str = os.environ.get("JARVIS_LOCAL_WHISPER_MODEL", "base.en").strip().lower()

    @property
    def frame_duration_ms(self) -> float:
        return (self.chunk_samples / self.sample_rate) * 1000.0

    @property
    def pre_speech_frames(self) -> int:
        return max(1, int(math.ceil(self.pre_speech_ms / self.frame_duration_ms)))

    @property
    def silence_frames_limit(self) -> int:
        return max(1, int(math.ceil(self.silence_timeout_ms / self.frame_duration_ms)))

    @property
    def min_speech_frames(self) -> int:
        return max(1, int(math.ceil(self.min_speech_duration_ms / self.frame_duration_ms)))


class VADState(str, Enum):
    """VAD state machine states."""
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    COLLECTING_SPEECH = "COLLECTING_SPEECH"
    PROCESSING = "PROCESSING"


# ── Stage 1: Audio Input Stage ────────────────────────────────────────────────

class AudioInputStage:
    """
    Handles microphone capture, frame ingestion, and rolling pre-speech ring buffer.
    """

    def __init__(self, config: VADConfig):
        self.config = config
        self._ring_buffer: deque[np.ndarray] = deque(maxlen=config.pre_speech_frames)
        self._lock = threading.Lock()

    def update_config(self, config: VADConfig) -> None:
        with self._lock:
            self.config = config
            old_items = list(self._ring_buffer)
            self._ring_buffer = deque(old_items, maxlen=config.pre_speech_frames)

    def push_frame(self, raw_chunk: np.ndarray) -> None:
        """Store int16 or float32 frame into the rolling pre-speech ring buffer."""
        with self._lock:
            self._ring_buffer.append(raw_chunk)

    def get_pre_speech_frames(self) -> list[np.ndarray]:
        """Retrieve a copy of pre-speech frames preserving leading plosives."""
        with self._lock:
            return list(self._ring_buffer)

    def clear(self) -> None:
        with self._lock:
            self._ring_buffer.clear()


# ── Stage 2: Energy Estimation Module ─────────────────────────────────────────

class EnergyEstimator:
    """
    Calculates Short-Term Energy (STE), Root-Mean-Square (RMS), Peak Amplitude,
    dBFS, and maintains an adaptive background noise floor tracker.
    """

    def __init__(self, config: VADConfig):
        self.config = config
        self.noise_floor: float = config.initial_noise_floor
        self._lock = threading.Lock()

    def update_config(self, config: VADConfig) -> None:
        with self._lock:
            self.config = config

    def estimate(self, float32_audio: np.ndarray, is_speech: bool = False) -> Tuple[float, float, float, bool]:
        """
        Calculates:
        - rms: Root-Mean-Square volume
        - dbfs: Logarithmic decibel relative to full scale
        - snr_db: Signal-to-Noise Ratio relative to tracked ambient floor
        - is_loud_enough: Boolean indicating if energy passes gating threshold
        """
        if float32_audio.size == 0:
            return 0.0, -100.0, 0.0, False

        # 1. RMS Calculation
        rms = float(np.sqrt(np.mean(float32_audio ** 2)))

        # 2. Log Energy (dBFS)
        dbfs = 20.0 * math.log10(max(rms, 1e-6))

        with self._lock:
            # 3. Adaptive Noise Floor Tracking (update during non-speech periods)
            if not is_speech and rms > 0.0:
                alpha = self.config.noise_floor_alpha
                # Gently adapt towards ambient floor
                self.noise_floor = (alpha * self.noise_floor) + ((1.0 - alpha) * rms)
                # Keep noise floor within realistic bounds
                self.noise_floor = max(0.0005, min(self.noise_floor, 0.05))

            # 4. Signal-to-Noise Ratio in dB
            snr_db = 20.0 * math.log10(max(rms / max(self.noise_floor, 1e-6), 1.0))

            # 5. Energy Gating Decision
            is_loud_enough = (rms >= self.config.min_energy_rms) and (snr_db >= self.config.min_snr_db)

        return rms, dbfs, snr_db, is_loud_enough


# ── Stage 3: Spectral Analysis Component ──────────────────────────────────────

class SpectralAnalyzer:
    """
    Distinguishes human speech from stationary noise (fans, air conditioning)
    and non-speech transients using frequency domain spectral metrics.
    """

    def __init__(self, config: VADConfig):
        self.config = config
        self._fft_freqs = np.fft.rfftfreq(config.chunk_samples, d=1.0 / config.sample_rate)

        # Precompute band mask for human vocal tract frequencies (300Hz - 3400Hz)
        self._speech_mask = (self._fft_freqs >= config.speech_band_low_hz) & (
            self._fft_freqs <= config.speech_band_high_hz
        )

    def update_config(self, config: VADConfig) -> None:
        self.config = config
        self._fft_freqs = np.fft.rfftfreq(config.chunk_samples, d=1.0 / config.sample_rate)
        self._speech_mask = (self._fft_freqs >= config.speech_band_low_hz) & (
            self._fft_freqs <= config.speech_band_high_hz
        )

    def analyze(self, float32_audio: np.ndarray) -> Tuple[float, float, float, bool]:
        """
        Calculates:
        - speech_band_ratio: Energy concentration in human formant band (300Hz-3400Hz)
        - zcr: Zero-Crossing Rate
        - spectral_centroid: Center frequency of the spectrum
        - is_speech_spectrum: Boolean indicating if spectral profile matches human speech
        """
        n = len(float32_audio)
        if n == 0:
            return 0.0, 0.0, 0.0, False

        # 1. Zero-Crossing Rate (ZCR)
        signs = np.sign(float32_audio)
        signs[signs == 0] = 1
        zcr = float(np.sum(np.abs(np.diff(signs))) / (2.0 * n))

        # 2. Fast Fourier Transform
        windowed = float32_audio * np.hanning(n)
        fft_magnitudes = np.abs(np.fft.rfft(windowed))
        power_spectrum = fft_magnitudes ** 2
        total_power = float(np.sum(power_spectrum))

        if total_power < 1e-8:
            return 0.0, zcr, 0.0, False

        # 3. Human Speech Formant Band Ratio
        speech_power = float(np.sum(power_spectrum[self._speech_mask]))
        speech_band_ratio = speech_power / total_power

        # 4. Spectral Centroid
        centroid = float(np.sum(self._fft_freqs * fft_magnitudes) / np.sum(fft_magnitudes))

        # 5. Spectral Speech Validation
        is_speech_spectrum = (
            speech_band_ratio >= self.config.min_speech_band_ratio
            and self.config.min_zcr <= zcr <= self.config.max_zcr
        )

        return speech_band_ratio, zcr, centroid, is_speech_spectrum


# ── Stage 4: Silero Neural Model Wrapper ──────────────────────────────────────

class SileroVAD:
    """Evaluates Silero VAD neural probability estimator."""

    def __init__(self):
        self._model = None
        self._lock = threading.Lock()
        self._load_model()

    def _load_model(self) -> None:
        with self._lock:
            if self._model is not None:
                return

            local_cache_dirs = [
                os.path.expanduser("~/.cache/torch/hub/snakers4_silero-vad_master"),
                os.path.expanduser("~/.cache/torch/hub/snakers4_silero-vad_v4.0"),
            ]
            for cache_dir in local_cache_dirs:
                if os.path.isdir(cache_dir):
                    try:
                        model, _ = torch.hub.load(
                            repo_or_dir=cache_dir,
                            model="silero_vad",
                            source="local",
                            trust_repo=True,
                        )
                        model.eval()
                        self._model = model
                        print(f"[SileroVAD] Loaded offline neural VAD model from {cache_dir}.")
                        return
                    except Exception as exc:
                        print(f"[SileroVAD] Local cache load warning ({exc}), trying fallback...")

            try:
                model, _ = torch.hub.load(
                    repo_or_dir="snakers4/silero-vad",
                    model="silero_vad",
                    force_reload=False,
                    trust_repo=True,
                )
                model.eval()
                self._model = model
                print("[SileroVAD] Loaded neural VAD model from torch.hub.")
            except Exception as exc:
                print(f"[SileroVAD] Warning: Failed to load Silero VAD ({exc}). Energy fallback active.")
                self._model = None

    def reset_states(self) -> None:
        if self._model is not None and hasattr(self._model, "reset_states"):
            try:
                self._model.reset_states()
            except Exception:
                pass

    def predict_chunk(self, float32_audio: np.ndarray, sample_rate: int = 16000) -> float:
        """
        Evaluate speech probability on a 512-sample float32 array in [-1.0, 1.0].
        Executes in ~0.01ms on modern CPU.
        """
        if self._model is None:
            return 0.5  # Neutral fallback when neural model unavailable

        try:
            tensor = torch.from_numpy(float32_audio).unsqueeze(0)
            with torch.no_grad():
                prob = self._model(tensor, sample_rate).item()
            return float(prob)
        except Exception as exc:
            print(f"[SileroVAD] Inference error: {exc}")
            return 0.0


# ── Stage 5: Classification Logic & Multi-Feature Fusion ──────────────────────

class VADClassifier:
    """
    Fuses Energy, Spectral, and Neural features using dual-threshold hysteresis.
    Determines whether a frame is speech, active continuation, or silence.
    """

    def __init__(self, config: VADConfig, silero: SileroVAD, energy: EnergyEstimator, spectral: SpectralAnalyzer):
        self.config = config
        self.silero = silero
        self.energy = energy
        self.spectral = spectral

    def update_config(self, config: VADConfig) -> None:
        self.config = config

    def classify_frame(
        self,
        float32_audio: np.ndarray,
        currently_in_speech: bool = False,
    ) -> Tuple[bool, float, dict]:
        """
        Evaluates multi-feature fusion:
        1. Energy check (RMS, SNR above dynamic noise floor)
        2. Spectral check (Formant band ratio, ZCR)
        3. Neural probability (Silero VAD)

        Returns:
        - is_speech: Final classification determination
        - neural_prob: Neural probability score
        - metrics: Diagnostic metrics dict
        """
        # 1. Energy Analysis
        rms, dbfs, snr_db, is_loud = self.energy.estimate(float32_audio, is_speech=currently_in_speech)

        # 2. Spectral Analysis
        speech_ratio, zcr, centroid, is_speech_spectral = self.spectral.analyze(float32_audio)

        # Fast gate: If sound is beneath energy floor, bypass neural inference
        if not is_loud and not currently_in_speech:
            metrics = {
                "rms": rms, "dbfs": dbfs, "snr_db": snr_db,
                "speech_ratio": speech_ratio, "zcr": zcr, "centroid": centroid,
                "neural_prob": 0.0, "is_speech": False,
            }
            return False, 0.0, metrics

        # 3. Neural Prediction
        neural_prob = self.silero.predict_chunk(float32_audio, self.config.sample_rate)

        # 4. Dual-Threshold Hysteresis Decision (Schmitt Trigger)
        threshold = self.config.speech_sustain_prob if currently_in_speech else self.config.speech_onset_prob

        # Multi-feature fusion:
        # High neural confidence overrides subtle spectral shifts (e.g. whispered fricatives);
        # In marginal neural ranges, spectral resonance confirms speech vs AC hum.
        if currently_in_speech:
            is_speech = (neural_prob >= threshold) or (is_loud and is_speech_spectral and neural_prob >= 0.25)
        else:
            is_speech = (neural_prob >= threshold) and (is_loud or speech_ratio >= self.config.min_speech_band_ratio)

        metrics = {
            "rms": rms,
            "dbfs": dbfs,
            "snr_db": snr_db,
            "speech_ratio": speech_ratio,
            "zcr": zcr,
            "centroid": centroid,
            "neural_prob": neural_prob,
            "is_speech": is_speech,
        }
        return is_speech, neural_prob, metrics


# ── Whisper (faster-whisper) Background STT ───────────────────────────────────

class WhisperSTT:
    """Thread-safe lazy faster-whisper loader."""

    def __init__(self, model_name: str = "base.en"):
        self.model_name = model_name if model_name in {"base.en", "small.en"} else "base.en"
        self._model = None
        self._lock = threading.Lock()

    def _get_model(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from faster_whisper import WhisperModel
                print(f"[WhisperSTT] Initializing faster-whisper ({self.model_name}) on CPU [int8]...")
                self._model = WhisperModel(
                    self.model_name,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=4,
                )
                print("[WhisperSTT] Model ready.")
            except Exception as exc:
                print(f"[WhisperSTT] Failed to load faster-whisper: {exc}")
                self._model = None
        return self._model

    def transcribe(self, audio_float32: np.ndarray) -> str:
        """Transcribe normalized float32 audio [-1.0, 1.0] at 16kHz."""
        model = self._get_model()
        if model is None or audio_float32.size == 0:
            return ""

        try:
            segments, _ = model.transcribe(
                audio_float32,
                language="en",
                beam_size=1,
                condition_on_previous_text=False,
                vad_filter=False,  # Audio has already been segmented by VAD
                initial_prompt="The assistant is named Jarvis.",
            )
            raw_text = " ".join((seg.text or "").strip() for seg in segments).strip()

            try:
                from stt.correct import correct_transcript
                from stt.filter import is_phantom_transcript, is_whisper_hallucination

                if is_whisper_hallucination(raw_text):
                    return ""
                cleaned = correct_transcript(raw_text)
                if is_phantom_transcript(cleaned):
                    return ""
                return cleaned
            except ImportError:
                return raw_text

        except Exception as exc:
            print(f"[WhisperSTT] Transcription error: {exc}")
            return ""


# ── Pocket TTS Interrupter ─────────────────────────────────────────────────────

def is_pocket_tts_speaking() -> bool:
    try:
        from tts.pocket_tts import is_speaking
        return is_speaking()
    except Exception:
        return False


def terminate_pocket_tts_playback() -> None:
    try:
        sd.stop()
    except Exception as exc:
        print(f"[Barge-In] sd.stop warning: {exc}")

    try:
        from tts.pocket_tts import stop_speech
        stop_speech()
    except Exception as exc:
        print(f"[Barge-In] Pocket TTS stop error: {exc}")

    print("[Barge-In] Pocket TTS audio terminated instantly.")


# ── Complete Integrated VAD Pipeline ──────────────────────────────────────────

class JarvisVADPipeline:
    """
    Real-Time Zero-Lag VAD Audio Pipeline:
    - Stage 1: Audio Input Stage (16kHz 16-bit mono capture, 300ms pre-speech buffer)
    - Stage 2: Energy Estimation Module (RMS, dBFS, SNR, dynamic noise floor)
    - Stage 3: Spectral Analysis Component (Formant ratio, ZCR, Centroid)
    - Stage 4: Silero Neural Model Wrapper
    - Stage 5: Classification Logic & Multi-Feature Fusion
    """

    def __init__(
        self,
        config: Optional[VADConfig] = None,
        on_speech_start: Optional[Callable[[], None]] = None,
        on_speech_end: Optional[Callable[[np.ndarray], None]] = None,
        on_transcript: Optional[Callable[[str], None]] = None,
        on_barge_in: Optional[Callable[[], None]] = None,
        on_state_change: Optional[Callable[[VADState], None]] = None,
        on_metrics: Optional[Callable[[dict], None]] = None,
        device_index: Optional[int] = None,
    ):
        self.config = config or VADConfig()
        self.on_speech_start = on_speech_start
        self.on_speech_end = on_speech_end
        self.on_transcript = on_transcript
        self.on_barge_in = on_barge_in
        self.on_state_change = on_state_change
        self.on_metrics = on_metrics
        self.device_index = device_index

        # Instantiate pipeline stages
        self.input_stage = AudioInputStage(self.config)
        self.energy = EnergyEstimator(self.config)
        self.spectral = SpectralAnalyzer(self.config)
        self.silero = SileroVAD()
        self.classifier = VADClassifier(self.config, self.silero, self.energy, self.spectral)
        self.stt = WhisperSTT(self.config.whisper_model)

        self.state: VADState = VADState.LISTENING

        # Active collection state
        self._speech_buffer: list[np.ndarray] = []
        self._silence_frames: int = 0
        self._speech_frames_count: int = 0
        self._barge_in_consecutive: int = 0

        # Concurrency & Audio Stream
        self._audio_queue: queue.Queue = queue.Queue(maxsize=120)
        self._is_running: bool = False
        self._stream: Optional[sd.InputStream] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="JarvisSTT")
        self._lock = threading.Lock()

    def update_config(self, **kwargs) -> None:
        """Dynamically update any tunable thresholds or timing parameters at runtime."""
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self.config, k):
                    setattr(self.config, k, v)
                    print(f"[JarvisVAD] Config updated: {k} = {v}")
            self.input_stage.update_config(self.config)
            self.energy.update_config(self.config)
            self.spectral.update_config(self.config)
            self.classifier.update_config(self.config)

    def _set_state(self, new_state: VADState) -> None:
        if self.state != new_state:
            old_state = self.state
            self.state = new_state
            print(f"[JarvisVAD] State Transition: {old_state.value} -> {new_state.value}")
            if self.on_state_change:
                try:
                    self.on_state_change(new_state)
                except Exception as exc:
                    print(f"[JarvisVAD] on_state_change error: {exc}")

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if not self._is_running:
            return
        chunk = indata[:, 0].copy()
        try:
            self._audio_queue.put_nowait(chunk)
        except queue.Full:
            try:
                self._audio_queue.get_nowait()
                self._audio_queue.put_nowait(chunk)
            except Exception:
                pass

    def _process_loop(self) -> None:
        print("[JarvisVAD] Processing worker started.")

        while self._is_running:
            try:
                chunk = self._audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            # Convert 16-bit PCM integer to normalized float32 [-1.0, 1.0]
            float32_chunk = chunk.astype(np.float32) / 32768.0

            currently_in_speech = (self.state == VADState.COLLECTING_SPEECH)

            # Classify frame using fused Energy + Spectral + Neural logic
            is_speech, prob, metrics = self.classifier.classify_frame(
                float32_chunk,
                currently_in_speech=currently_in_speech,
            )

            if self.on_metrics:
                try:
                    self.on_metrics(metrics)
                except Exception:
                    pass

            tts_active = is_pocket_tts_speaking()

            # ── 1. Barge-In Interruption Engine ────────────────────────────
            if tts_active:
                if prob >= self.config.barge_in_prob:
                    self._barge_in_consecutive += 1
                else:
                    self._barge_in_consecutive = 0

                if self._barge_in_consecutive >= self.config.barge_in_consecutive_frames:
                    print(f"[JarvisVAD] Barge-in triggered! (prob={prob:.2f})")
                    terminate_pocket_tts_playback()
                    self._barge_in_consecutive = 0

                    if self.on_barge_in:
                        try:
                            self.on_barge_in()
                        except Exception as exc:
                            print(f"[JarvisVAD] on_barge_in error: {exc}")

                    self._start_speech_collection(chunk)
                    continue

            # ── 2. State Machine Transitions ───────────────────────────────
            if self.state in (VADState.IDLE, VADState.LISTENING):
                # Maintain rolling pre-speech ring buffer
                self.input_stage.push_frame(chunk)

                if not tts_active and is_speech:
                    self._start_speech_collection(chunk)

            elif self.state == VADState.COLLECTING_SPEECH:
                self._speech_buffer.append(chunk)

                if is_speech:
                    self._silence_frames = 0
                    self._speech_frames_count += 1
                else:
                    self._silence_frames += 1

                # Check if hangover silence duration has elapsed
                if self._silence_frames >= self.config.silence_frames_limit:
                    self._finalize_speech_collection()
                # Safety cutoff for runaway recordings
                elif len(self._speech_buffer) * self.config.frame_duration_ms > (self.config.max_speech_duration_s * 1000.0):
                    print("[JarvisVAD] Max speech duration reached — forcing finalization.")
                    self._finalize_speech_collection()

            elif self.state == VADState.PROCESSING:
                # Maintain pre-speech buffer during transcription
                self.input_stage.push_frame(chunk)

        print("[JarvisVAD] Processing worker terminated.")

    def _start_speech_collection(self, trigger_chunk: np.ndarray) -> None:
        self._set_state(VADState.COLLECTING_SPEECH)
        self._silence_frames = 0
        self._speech_frames_count = 1

        # Seed with rolling pre-speech ring buffer (prevents clipping plosives)
        self._speech_buffer = self.input_stage.get_pre_speech_frames()
        self._speech_buffer.append(trigger_chunk)

        if self.on_speech_start:
            try:
                self.on_speech_start()
            except Exception as exc:
                print(f"[JarvisVAD] on_speech_start error: {exc}")

    def _finalize_speech_collection(self) -> None:
        total_frames = len(self._speech_buffer)
        speech_duration_ms = self._speech_frames_count * self.config.frame_duration_ms

        # Discard spurious noises shorter than tunable minimum speech duration
        if self._speech_frames_count < self.config.min_speech_frames:
            print(
                f"[JarvisVAD] Discarded noise ({speech_duration_ms:.0f}ms < "
                f"{self.config.min_speech_duration_ms}ms)."
            )
            self._speech_buffer.clear()
            self._set_state(VADState.LISTENING)
            self.silero.reset_states()
            return

        print(
            f"[JarvisVAD] Speech finalized. Total audio: {total_frames * self.config.frame_duration_ms:.0f}ms "
            f"(active speech: {speech_duration_ms:.0f}ms, silence: {self._silence_frames * self.config.frame_duration_ms:.0f}ms)"
        )

        concatenated_pcm = np.concatenate(self._speech_buffer)
        audio_float32 = concatenated_pcm.astype(np.float32) / 32768.0

        self._speech_buffer.clear()
        self._set_state(VADState.PROCESSING)

        if self.on_speech_end:
            try:
                self.on_speech_end(audio_float32)
            except Exception as exc:
                print(f"[JarvisVAD] on_speech_end error: {exc}")

        # Dispatch non-blockingly to background thread pool
        self._executor.submit(self._dispatch_whisper, audio_float32)

    def _dispatch_whisper(self, audio_float32: np.ndarray) -> None:
        t0 = time.perf_counter()
        try:
            transcript = self.stt.transcribe(audio_float32)
            duration_ms = (time.perf_counter() - t0) * 1000.0
            print(f"[JarvisVAD] Whisper ({duration_ms:.0f}ms): {transcript!r}")

            if transcript and self.on_transcript:
                try:
                    self.on_transcript(transcript)
                except Exception as exc:
                    print(f"[JarvisVAD] on_transcript error: {exc}")

        except Exception as exc:
            print(f"[JarvisVAD] Whisper execution error: {exc}")
        finally:
            self.silero.reset_states()
            with self._lock:
                if self.state == VADState.PROCESSING:
                    self._set_state(VADState.LISTENING)

    def start(self) -> None:
        with self._lock:
            if self._is_running:
                return

            self._is_running = True
            self.input_stage.clear()
            self._speech_buffer.clear()
            self._silence_frames = 0
            self._speech_frames_count = 0
            self._barge_in_consecutive = 0
            self.state = VADState.LISTENING

            mic_dev = self.device_index
            if mic_dev is None:
                try:
                    from stt.devices import pick_input_device
                    mic_dev = pick_input_device()
                except Exception:
                    mic_dev = None

            self._stream = sd.InputStream(
                device=mic_dev,
                samplerate=self.config.sample_rate,
                channels=1,
                dtype="int16",
                blocksize=self.config.chunk_samples,
                callback=self._audio_callback,
                latency="low",
            )
            self._stream.start()

            self._worker_thread = threading.Thread(
                target=self._process_loop,
                daemon=True,
                name="JarvisVADWorker",
            )
            self._worker_thread.start()
            print(f"[JarvisVAD] Pipeline active on device [{mic_dev}]. Listening...")

    def stop(self) -> None:
        with self._lock:
            if not self._is_running:
                return
            self._is_running = False

            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None

            if self._worker_thread is not None:
                self._worker_thread.join(timeout=1.5)
                self._worker_thread = None

            self._set_state(VADState.IDLE)
            print("[JarvisVAD] Pipeline stopped.")

    async def listen_one_utterance_async(self, timeout: float = 15.0) -> str:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()

        original_cb = self.on_transcript

        def _transcript_done(text: str):
            if original_cb:
                try:
                    original_cb(text)
                except Exception:
                    pass
            if not future.done():
                loop.call_soon_threadsafe(future.set_result, text)

        self.on_transcript = _transcript_done
        was_running = self._is_running
        if not was_running:
            self.start()

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            print("[JarvisVAD] Single utterance listen timed out.")
            return ""
        finally:
            self.on_transcript = original_cb
            if not was_running:
                self.stop()


# ── CLI Self-Test ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import signal

    print("\n" + "=" * 65)
    print("  JARVIS 5-Stage VAD (Energy + Spectral + Silero + Whisper)")
    print("=" * 65)

    cfg = VADConfig(
        speech_onset_prob=0.60,
        speech_sustain_prob=0.40,
        silence_timeout_ms=600,
        min_speech_duration_ms=350,
    )

    def on_metrics(m):
        if m["is_speech"]:
            print(
                f"\r[VAD ACTIVE] RMS={m['rms']:.4f} ({m['dbfs']:.1f}dBFS) | "
                f"SNR={m['snr_db']:.1f}dB | BandRatio={m['speech_ratio']:.2f} | "
                f"Prob={m['neural_prob']:.2f}   ",
                end="",
                flush=True,
            )

    pipeline = JarvisVADPipeline(
        config=cfg,
        on_speech_start=lambda: print("\n>>> [SPEECH START]"),
        on_speech_end=lambda a: print(f"\n>>> [SPEECH END] Buffer: {len(a)/16000:.2f}s"),
        on_transcript=lambda t: print(f"\n>>> [TRANSCRIPT] \"{t}\"\n"),
        on_barge_in=lambda: print("\n>>> [BARGE-IN] TTS Interrupted!"),
        on_metrics=on_metrics,
    )

    pipeline.start()
    print("\nListening with real-time Energy & Spectral analysis. Press Ctrl+C to stop.\n")

    stop_event = threading.Event()
    signal.signal(signal.SIGINT, lambda s, f: stop_event.set())

    try:
        while not stop_event.is_set():
            time.sleep(0.1)
    finally:
        pipeline.stop()
        print("\nShutdown complete.")
