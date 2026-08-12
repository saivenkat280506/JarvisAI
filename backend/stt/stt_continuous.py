"""
stt.py — Low-Latency Speech-to-Text Module
==========================================
CPU-based | Free | Offline | Unlimited Usage
Model: faster-whisper (CTranslate2 int8 quantized)
VAD:   Silero VAD (detects speech end instantly)

Quick Integration:
    from stt import STT
    stt = STT(on_text=print)
    stt.start()
"""

import threading
import queue
import time
import sys
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from typing import Callable, Optional


# ─── Default Config ────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    # Model: "tiny" ~fastest | "base" ~best CPU balance | "small" ~higher accuracy
    "model_size":        "base",
    "device":            "cpu",
    "compute_type":      "int8",        # quantized → fast on CPU
    "language":          None,          # None = auto-detect, or "en", "hi", etc.

    # Audio
    "sample_rate":       16000,
    "chunk_ms":          32,            # VAD chunk size in ms (32ms * 16kHz = 512 samples)
    "channels":          1,

    # VAD (Voice Activity Detection)
    "vad_threshold":     0.5,           # 0-1, higher = less sensitive
    "silence_ms":        500,           # ms of silence before finalizing
    "min_speech_ms":     200,           # ignore clips shorter than this

    # Transcription
    "beam_size":         1,             # 1 = fastest, 5 = more accurate
    "condition_on_prev": False,         # faster when False
    "word_timestamps":   False,
}


# ─── STT Class ─────────────────────────────────────────────────────────────────

class STT:
    """
    Drop-in low-latency Speech-to-Text.

    Usage:
        stt = STT(on_text=lambda text: print(f"You said: {text}"))
        stt.start()
        # ... your app runs ...
        stt.stop()

    Callbacks:
        on_text(text: str)              — final transcription
        on_partial(text: str)           — partial (interim) result
        on_recording_start()            — user started speaking
        on_recording_stop()             — user stopped speaking
    """

    def __init__(
        self,
        on_text:           Callable[[str], None]        = None,
        on_partial:        Callable[[str], None]        = None,
        on_recording_start: Callable[[], None]          = None,
        on_recording_stop:  Callable[[], None]          = None,
        config:            dict                         = None,
    ):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

        self.cfg = {**DEFAULT_CONFIG, **(config or {})}
        self.on_text            = on_text           or (lambda t: None)
        self.on_partial         = on_partial        or (lambda t: None)
        self.on_recording_start = on_recording_start or (lambda: None)
        self.on_recording_stop  = on_recording_stop  or (lambda: None)

        self._audio_q:  queue.Queue = queue.Queue()
        self._transcription_q: queue.Queue = queue.Queue()
        self._running:  bool        = False
        self._speaking: bool        = False
        self._speech_buffer: list   = []
        self._silence_chunks: int   = 0
        self._last_partial_time: float = 0.0

        print("[STT] Loading model…", end=" ", flush=True)
        self.model = WhisperModel(
            self.cfg["model_size"],
            device=self.cfg["device"],
            compute_type=self.cfg["compute_type"],
        )
        print("ready OK")
        self._vad_model, self._vad_utils = self._load_vad()
        print("[STT] VAD ready OK")

    # ── Public API ──────────────────────────────────────────────────────────────

    def start(self, block: bool = False):
        """Start listening. block=True to wait (e.g. in __main__)."""
        self._stream = sd.InputStream(
            samplerate=self.cfg["sample_rate"],
            channels=self.cfg["channels"],
            dtype="float32",
            blocksize=int(self.cfg["sample_rate"] * self.cfg["chunk_ms"] / 1000),
            callback=self._audio_callback,
        )
        self._running = True
        self._transcription_thread = threading.Thread(target=self._transcription_worker, daemon=True)
        self._transcription_thread.start()
        self._transcribe_thread = threading.Thread(target=self._transcribe_loop, daemon=True)
        self._transcribe_thread.start()
        try:
            self._stream.start()
        except Exception:
            self._running = False
            self._transcription_thread.join(timeout=1.0)
            self._transcribe_thread.join(timeout=1.0)
            self._stream.close()
            raise
        print("[STT] Listening… (Ctrl+C to stop)")
        if block:
            try:
                while self._running:
                    time.sleep(0.1)
            except KeyboardInterrupt:
                self.stop()

    def stop(self):
        """Stop listening and clean up."""
        self._running = False
        if hasattr(self, "_stream"):
            self._stream.stop()
            self._stream.close()
        if hasattr(self, "_transcription_thread"):
            self._transcription_thread.join(timeout=2.0)
        if hasattr(self, "_transcribe_thread"):
            self._transcribe_thread.join(timeout=2.0)
        print("\n[STT] Stopped.")

    def transcribe_file(self, path: str) -> str:
        """Transcribe an audio file and return text."""
        segments, _ = self.model.transcribe(
            path,
            language=self.cfg["language"],
            beam_size=self.cfg["beam_size"],
            condition_on_previous_text=self.cfg["condition_on_prev"],
            word_timestamps=self.cfg["word_timestamps"],
        )
        return " ".join(s.text.strip() for s in segments)

    def transcribe_array(self, audio: np.ndarray) -> str:
        """Transcribe a numpy float32 array (16kHz mono)."""
        segments, _ = self.model.transcribe(
            audio,
            language=self.cfg["language"],
            beam_size=self.cfg["beam_size"],
            condition_on_previous_text=self.cfg["condition_on_prev"],
        )
        return " ".join(s.text.strip() for s in segments)

    # ── Internal ────────────────────────────────────────────────────────────────

    def _load_vad(self):
        import torch
        model, utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            force_reload=False,
            trust_repo=True,
        )
        return model, utils

    def _is_speech(self, chunk: np.ndarray) -> bool:
        import torch
        tensor = torch.from_numpy(chunk).unsqueeze(0)
        prob = self._vad_model(tensor, self.cfg["sample_rate"]).item()
        return prob > self.cfg["vad_threshold"]

    def _transcription_worker(self):
        """Worker thread that handles actual Whisper transcriptions to avoid blocking VAD."""
        while self._running:
            try:
                item = self._transcription_q.get(timeout=0.5)
            except queue.Empty:
                continue
            
            task_type = item["type"]
            audio = item["audio"]
            
            if audio is None or len(audio) == 0:
                continue
                
            t0 = time.perf_counter()
            text = self.transcribe_array(audio)
            latency = (time.perf_counter() - t0) * 1000
            
            if task_type == "partial":
                if text.strip() and self._running:
                    self.on_partial(text.strip())
            elif task_type == "final":
                if text.strip() and self._running:
                    print(f"[STT] ({latency:.0f}ms) {text.strip()}")
                    self.on_text(text.strip())

    def _audio_callback(self, indata, frames, time_info, status):
        if self._running:
            self._audio_q.put(indata[:, 0].copy())

    def _transcribe_loop(self):
        sr = self.cfg["sample_rate"]
        chunk_ms = self.cfg["chunk_ms"]
        silence_chunks_needed = int(self.cfg["silence_ms"] / chunk_ms)
        min_speech_chunks = int(self.cfg["min_speech_ms"] / chunk_ms)

        while self._running:
            try:
                chunk = self._audio_q.get(timeout=0.5)
            except queue.Empty:
                continue

            speech = self._is_speech(chunk)

            if speech:
                if not self._speaking:
                    self._speaking = True
                    self._speech_buffer = []
                    self._silence_chunks = 0
                    self.on_recording_start()
                self._speech_buffer.append(chunk)
                self._silence_chunks = 0
                
                # Periodic partial transcription (every ~800ms)
                now = time.time()
                if now - self._last_partial_time > 0.8:
                    audio_partial = np.concatenate(self._speech_buffer)
                    self._transcription_q.put({"type": "partial", "audio": audio_partial})
                    self._last_partial_time = now

            elif self._speaking:
                self._speech_buffer.append(chunk)
                self._silence_chunks += 1

                if self._silence_chunks >= silence_chunks_needed:
                    self._speaking = False
                    self.on_recording_stop()

                    if len(self._speech_buffer) >= min_speech_chunks:
                        audio_final = np.concatenate(self._speech_buffer)
                        self._transcription_q.put({"type": "final", "audio": audio_final})

                    self._speech_buffer = []
                    self._silence_chunks = 0
