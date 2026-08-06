"""
runtime_state.py — Shared Runtime Flags for JARVIS
===================================================
Centralized state: SystemState enum, processing flags, stop event for STT abort.
"""

import threading
from enum import Enum


class SystemState(Enum):
    IDLE = "idle"
    IDLE_LISTENING = "idle_listening"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    PROCESSING = "thinking"
    SPEAKING = "talking"


class RuntimeFlags:
    def __init__(self):
        self.is_listening: bool = False
        self.is_processing: bool = False
        self.force_listen_trigger: bool = False
        self.stop_listen_trigger: bool = False
        self.continuous_voice_mode: bool = False
        self.voice_session_active: bool = False
        self.speak_epoch: int = 0
        self.mic_muted: bool = False
        self.shutdown_requested: bool = False

        self.last_request_time: float = 0
        self.last_response_time: float = 0
        self.last_user_input: str = ""
        self.last_assistant_response: str = ""
        self.last_intent: str = ""
        self.processed_ids: set = set()

        self.stop_event: threading.Event = threading.Event()
        self.state_lock: threading.Lock = threading.Lock()

    def reset_processing_state(self):
        self.force_listen_trigger = False
        self.stop_listen_trigger = True
        self.continuous_voice_mode = False
        self.voice_session_active = False
        self.is_processing = False
        self.is_listening = False
        self.processed_ids.clear()
        self.speak_epoch += 1
        self.stop_event.set()

    def prepare_for_new_command(self):
        """Allow a fresh chat/text command after reset or stop."""
        self.stop_listen_trigger = False
        self.force_listen_trigger = False
        self.continuous_voice_mode = False
        self.voice_session_active = False
        self.is_processing = False
        self.is_listening = False
        self.stop_event.clear()


flags = RuntimeFlags()
