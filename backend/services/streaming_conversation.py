"""
streaming_conversation.py — Full-Duplex Streaming Conversational Architecture
=============================================================================
Manages simultaneous input/output streaming, low-latency interruptions (barge-in),
progressive token-to-speech pipelining, and dynamic conversational context resynchronization.

Core Architectural Subsystems:
1. Full-Duplex Input/Output Engine:
   - Continuous microphone ingestion & neural VAD (Silero) runs in parallel with
     active LLM token generation and TTS audio playback.
2. Progressive Token-to-Speech Pipeline:
   - Streams tokens from the LLM as they are generated.
   - Groups tokens into natural sentence/clause boundaries and dispatches to TTS
     progressively, minimizing Time-To-First-Audio (TTFA).
3. Instantaneous Barge-In & Cancellation Bus:
   - When the user interrupts, cancels the active LLM generation task (asyncio.Task.cancel()),
     kills the audio DAC output (sounddevice.stop()), and flushes queued audio buffers.
4. Dynamic Context Resynchronization:
   - Tracks the exact text that was voiced before the interruption point.
   - Updates conversation memory with the partial spoken response annotated with
     "[interrupted]" so the model knows what was heard vs aborted.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
import json
import re
import time
from typing import AsyncGenerator, Callable, Optional

import httpx

from stt.vad import JarvisVADPipeline, VADState, terminate_pocket_tts_playback, is_pocket_tts_speaking
from services.websocket_manager import manager


# ── Turn State ────────────────────────────────────────────────────────────────

class TurnState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"


@dataclass
class ConversationTurn:
    """Tracks state and playback progress for an active conversational turn."""
    turn_id: str
    user_query: str
    full_generated_text: str = ""
    spoken_text: str = ""
    is_interrupted: bool = False
    start_time: float = field(default_factory=time.time)


# ── Streaming Token Chunker (Token-to-Sentence) ───────────────────────────────

class SentenceChunker:
    """
    Progressively segments an incoming stream of tokens into natural sentence
    boundaries for low-latency TTS dispatch.
    """

    # Boundaries: periods, exclamation marks, question marks, newlines
    _SENTENCE_END = re.compile(r'([.!?\n]+)\s*')
    # Soft clause boundaries for long sentences (commas, semicolons after >= 6 words)
    _CLAUSE_END = re.compile(r'([,;:]+)\s*')

    def __init__(self, min_words: int = 4):
        self.min_words = min_words
        self._buffer: str = ""

    def push(self, token: str) -> list[str]:
        """Pushes a token and returns any complete sentences ready for speech."""
        self._buffer += token
        ready_sentences: list[str] = []

        while True:
            match = self._SENTENCE_END.search(self._buffer)
            if match:
                end_idx = match.end()
                sentence = self._buffer[:end_idx].strip()
                self._buffer = self._buffer[end_idx:]
                if sentence:
                    ready_sentences.append(sentence)
                continue

            # Fallback for long clauses to maintain streaming flow
            words = self._buffer.split()
            if len(words) >= 8:
                clause_match = self._CLAUSE_END.search(self._buffer)
                if clause_match and clause_match.start() > 15:
                    end_idx = clause_match.end()
                    clause = self._buffer[:end_idx].strip()
                    self._buffer = self._buffer[end_idx:]
                    if clause:
                        ready_sentences.append(clause)
                    continue

            break

        return ready_sentences

    def flush(self) -> list[str]:
        """Flushes any remaining text in the buffer at the end of the stream."""
        remaining = self._buffer.strip()
        self._buffer = ""
        return [remaining] if remaining else []


# ── Full-Duplex Streaming Conversation Orchestrator ───────────────────────────

class StreamingConversationOrchestrator:
    """
    Manages full-duplex conversational flow:
    - Continuous live VAD input
    - Cancellable streaming generation & playback
    - Real-time barge-in cancellation and context resynchronization
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen3:4b",
    ):
        self.base_url = base_url
        self.model = model

        self.current_state: TurnState = TurnState.IDLE
        self.active_turn: Optional[ConversationTurn] = None

        # Concurrency & Cancellation
        self._generation_task: Optional[asyncio.Task] = None
        self._playback_task: Optional[asyncio.Task] = None
        self._turn_lock = asyncio.Lock()
        self._epoch: int = 0

        # Conversation History: list of {"role": "user"|"assistant", "content": str}
        self.history: list[dict[str, str]] = []

        # Integrated Real-Time VAD
        self.vad_pipeline: Optional[JarvisVADPipeline] = None

    def initialize_vad(self) -> None:
        """Sets up the VAD pipeline with real-time barge-in callbacks."""
        self.vad_pipeline = JarvisVADPipeline(
            on_speech_start=self._on_user_speech_start_threadsafe,
            on_transcript=self._on_user_transcript_threadsafe,
            on_barge_in=self._on_barge_in_threadsafe,
        )

    # ── Thread-Safe VAD Callbacks ──────────────────────────────────────────────

    def _on_user_speech_start_threadsafe(self) -> None:
        """Called by audio thread the instant the user begins speaking."""
        # If the model is currently speaking or thinking, trigger immediate interruption
        if self.current_state in (TurnState.SPEAKING, TurnState.THINKING):
            print("[Orchestrator] User speech onset detected — triggering instant interruption.")
            asyncio.run_coroutine_threadsafe(self.handle_interruption(), asyncio.get_event_loop())

    def _on_barge_in_threadsafe(self) -> None:
        """Called when high-confidence barge-in thresholds are exceeded during audio output."""
        asyncio.run_coroutine_threadsafe(self.handle_interruption(), asyncio.get_event_loop())

    def _on_user_transcript_threadsafe(self, text: str) -> None:
        """Called when user utterance is finalized and transcribed."""
        clean = text.strip()
        if clean:
            asyncio.run_coroutine_threadsafe(self.submit_user_input(clean), asyncio.get_event_loop())

    # ── Interruption & Resynchronization Engine ────────────────────────────────

    async def handle_interruption(self) -> None:
        """
        Immediately halts active audio output, cancels in-flight LLM generation,
        and resynchronizes conversational context with what was actually heard.
        """
        async with self._turn_lock:
            self._epoch += 1
            current_epoch = self._epoch

            # 1. Instantly kill audio DAC playback and flush TTS queues
            terminate_pocket_tts_playback()

            # 2. Cancel in-flight LLM token generation
            if self._generation_task and not self._generation_task.done():
                self._generation_task.cancel()
                self._generation_task = None
                print("[Orchestrator] In-flight LLM generation task cancelled.")

            # 3. Cancel in-flight TTS playback task
            if self._playback_task and not self._playback_task.done():
                self._playback_task.cancel()
                self._playback_task = None
                print("[Orchestrator] In-flight playback task cancelled.")

            # 4. Context Resynchronization: truncate history to what was actually voiced
            if self.active_turn and not self.active_turn.is_interrupted:
                self.active_turn.is_interrupted = True
                spoken = self.active_turn.spoken_text.strip()
                if spoken:
                    resynced_content = f"{spoken} [interrupted]"
                    print(f"[Orchestrator] Resynchronized context to spoken cutoff: {resynced_content!r}")
                    # Update or append assistant's partial turn
                    if self.history and self.history[-1]["role"] == "assistant":
                        self.history[-1]["content"] = resynced_content
                    else:
                        self.history.append({"role": "assistant", "content": resynced_content})
                else:
                    # Model had not spoken any complete words yet before interruption
                    if self.history and self.history[-1]["role"] == "assistant":
                        self.history.pop()

            self.current_state = TurnState.INTERRUPTED
            await manager.broadcast_json({"type": "interrupted", "epoch": current_epoch})
            await manager.broadcast_state("idle_listening")

    # ── LLM Token Streaming ───────────────────────────────────────────────────

    async def _stream_llm_tokens(self, messages: list[dict], epoch: int) -> AsyncGenerator[str, None]:
        """Asynchronously streams tokens from local Ollama via NDJSON."""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": 0.3, "num_predict": 512},
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if self._epoch != epoch:
                        # Stale turn epoch — abort token consumption
                        break
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                        token = chunk.get("message", {}).get("content", "")
                        if token:
                            yield token
                        if chunk.get("done", False):
                            break
                    except Exception:
                        continue

    # ── Turn Execution & Progressive Speech ───────────────────────────────────

    async def submit_user_input(self, user_text: str) -> None:
        """
        Processes new user input:
        1. Resets turn state.
        2. Appends input to conversation context.
        3. Starts concurrent streaming LLM generation and progressive TTS output.
        """
        async with self._turn_lock:
            # Cancel any existing active turns
            if self.current_state in (TurnState.SPEAKING, TurnState.THINKING):
                await self.handle_interruption()

            self._epoch += 1
            turn_epoch = self._epoch
            turn_id = f"turn_{int(time.time()*1000)}"

            self.active_turn = ConversationTurn(turn_id=turn_id, user_query=user_text)
            self.history.append({"role": "user", "content": user_text})
            self.current_state = TurnState.THINKING

            await manager.broadcast_chat(user_text, role="user")
            await manager.broadcast_state("thinking")

            # Spawn concurrent turn executor
            self._generation_task = asyncio.create_task(
                self._run_streaming_turn(turn_epoch)
            )

    async def _run_streaming_turn(self, epoch: int) -> None:
        """Full-duplex streaming turn loop with progressive token-to-speech."""
        chunker = SentenceChunker(min_words=4)
        speech_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()

        # Start concurrent audio playback worker
        self._playback_task = asyncio.create_task(
            self._playback_worker(speech_queue, epoch)
        )

        try:
            full_text: list[str] = []

            # Stream tokens from model
            async for token in self._stream_llm_tokens(self.history, epoch):
                if self._epoch != epoch:
                    break

                full_text.append(token)
                if self.active_turn:
                    self.active_turn.full_generated_text = "".join(full_text)

                # Segment tokens into spoken sentences
                ready_sentences = chunker.push(token)
                for sentence in ready_sentences:
                    if self._epoch != epoch:
                        break
                    await speech_queue.put(sentence)

            # Flush trailing tokens
            if self._epoch == epoch:
                for sentence in chunker.flush():
                    await speech_queue.put(sentence)
                await speech_queue.put(None)  # Sentinel ending stream

            # Record final generated text in history if not interrupted
            if self._epoch == epoch and self.active_turn and not self.active_turn.is_interrupted:
                final_answer = "".join(full_text).strip()
                self.history.append({"role": "assistant", "content": final_answer})
                await manager.broadcast_chat(final_answer, role="assistant")

        except asyncio.CancelledError:
            print(f"[Orchestrator] Turn {epoch} generation cancelled.")
        finally:
            if self._epoch == epoch:
                self.current_state = TurnState.IDLE
                await manager.broadcast_state("idle_listening")

    async def _playback_worker(self, speech_queue: asyncio.Queue[Optional[str]], epoch: int) -> None:
        """Progressively speaks sentences as soon as they emerge from the LLM."""
        try:
            while True:
                if self._epoch != epoch:
                    break

                sentence = await speech_queue.get()
                if sentence is None:
                    break

                if self._epoch != epoch:
                    break

                self.current_state = TurnState.SPEAKING
                await manager.broadcast_state("talking")

                # Synthesize and speak sentence via Pocket TTS
                await self._speak_sentence(sentence, epoch)

                # Record spoken sentence into active turn tracking
                if self.active_turn and self._epoch == epoch:
                    self.active_turn.spoken_text += (" " + sentence).strip()

        except asyncio.CancelledError:
            print(f"[Orchestrator] Playback worker for epoch {epoch} cancelled.")

    async def _speak_sentence(self, sentence: str, epoch: int) -> None:
        """Dispatches an utterance to Pocket TTS off the asyncio loop."""
        def _speak_sync():
            if self._epoch != epoch:
                return
            try:
                from tts.pocket_tts import speak
                speak(sentence)
            except Exception as exc:
                print(f"[Orchestrator] TTS speak error: {exc}")

        await asyncio.to_thread(_speak_sync)

    # ── Lifecycle API ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Starts the VAD pipeline and puts orchestrator into listening mode."""
        if not self.vad_pipeline:
            self.initialize_vad()
        if self.vad_pipeline:
            self.vad_pipeline.start()
        self.current_state = TurnState.LISTENING
        print("[Orchestrator] Full-duplex streaming orchestrator started.")

    def stop(self) -> None:
        """Shuts down all tasks and stops audio capture."""
        if self._generation_task and not self._generation_task.done():
            self._generation_task.cancel()
        if self._playback_task and not self._playback_task.done():
            self._playback_task.cancel()
        if self.vad_pipeline:
            self.vad_pipeline.stop()
        self.current_state = TurnState.IDLE
        print("[Orchestrator] Orchestrator stopped.")
