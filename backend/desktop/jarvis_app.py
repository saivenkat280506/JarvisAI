"""
jarvis_app.py — Jarvis Native Windows Desktop Application
==========================================================
A stunning PyQt6 native Windows desktop app with:
- Left panel: Animated voice orb + waveform visualization
- Right panel: Control panel + chat interface
- Advanced voice mode with wake word detection
- Screen control capabilities
- Direct backend integration (no webview)
"""

import sys
import os
import json
import time
import threading
import random
import math
import requests
import re

# Add backend to path
BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, BACKEND_DIR)

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QTextEdit, QLineEdit, QPushButton, QLabel, QScrollArea,
    QFrame, QGraphicsDropShadowEffect, QSlider, QSizePolicy,
    QSpacerItem, QStackedWidget, QGridLayout
)
from PyQt6.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QPropertyAnimation,
    QEasingCurve, QRect, QPoint, QSize, pyqtProperty, QObject,
    QPointF
)
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QFont, QFontMetrics,
    QLinearGradient, QRadialGradient, QConicalGradient,
    QPainterPath, QPixmap, QIcon, QKeySequence
)

# ─── Constants ────────────────────────────────────────────────────────────────
BACKEND_URL = "http://127.0.0.1:8000"
WAKE_WORDS = ["hey jarvis", "ok jarvis", "jarvis", "yo jarvis", "wake up jarvis"]

ACCENT_BLUE = QColor(0, 180, 255)
ACCENT_CYAN = QColor(0, 255, 220)
ACCENT_PURPLE = QColor(120, 60, 255)
BG_DARK = QColor(8, 10, 18)
BG_PANEL = QColor(14, 18, 30)
BG_CARD = QColor(20, 26, 44)
TEXT_PRIMARY = QColor(230, 240, 255)
TEXT_SECONDARY = QColor(130, 150, 190)
BORDER_COLOR = QColor(40, 60, 100)


# ─── Voice Worker Thread ──────────────────────────────────────────────────────
class VoiceWorker(QObject):
    """Handles microphone input and wake word detection using the STT backend module."""
    transcribed = pyqtSignal(str)
    state_changed = pyqtSignal(str)  # idle, listening, thinking, talking, awake
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._active = False
        self.stt = None

    def start_listening(self):
        self._active = True
        try:
            from stt.stt_continuous import STT
            # Use 700ms silence so we get partial chunks fast, and aggregate them in the GUI thread
            self.stt = STT(
                on_text=self._on_txt,
                on_recording_start=lambda: self.state_changed.emit("listening"),
                on_recording_stop=lambda: self.state_changed.emit("thinking"),
                config={
                    "silence_ms": 700, 
                    "min_speech_ms": 300,
                }
            )
            # stt.start() is non-blocking since block=False by default, but it spawns threads
            self.stt.start(block=False)
        except Exception as e:
            self.error.emit(f"Voice error: {str(e)}")

    def stop_listening(self):
        self._active = False
        if getattr(self, "stt", None):
            self.stt.stop()
            self.stt = None

    def _on_txt(self, text):
        if self._active:
            self.transcribed.emit(text)


# ─── Backend Worker ───────────────────────────────────────────────────────────
class BackendWorker(QObject):
    """Sends requests to FastAPI backend and streams responses."""
    response_chunk = pyqtSignal(str, bool)  # text, is_done
    state_changed = pyqtSignal(str)
    error = pyqtSignal(str)

    def send_message(self, text: str):
        threading.Thread(
            target=self._stream_request,
            args=(text,),
            daemon=True
        ).start()

    def _stream_request(self, text: str):
        try:
            self.state_changed.emit("thinking")
            url = f"{BACKEND_URL}/chat"
            payload = {"text": text, "free_hands": False}

            with requests.post(url, json=payload, stream=True, timeout=60) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if line:
                        line_str = line.decode("utf-8")
                        if line_str.startswith("data: "):
                            data_str = line_str[6:]
                            try:
                                data = json.loads(data_str)
                                text_val = data.get("text", "")
                                done = data.get("done", False)
                                if "error" in data:
                                    self.error.emit(data["error"])
                                    return
                                self.response_chunk.emit(text_val, done)
                                if done:
                                    self.state_changed.emit("idle")
                            except json.JSONDecodeError:
                                pass

        except requests.ConnectionError:
            # Backend not running — run inline
            self._run_inline(text)
        except Exception as e:
            self.error.emit(f"Backend error: {str(e)}")
            self.state_changed.emit("idle")

    def _run_inline(self, text: str):
        """Direct brain invocation when backend server is not running."""
        try:
            import asyncio
            from brain.agent_graph import run_jarvis_agent

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(run_jarvis_agent(text))
            loop.close()

            self.response_chunk.emit(result, True)
            self.state_changed.emit("idle")

            # TTS
            try:
                from tts.pocket_tts import speak
                threading.Thread(target=speak, args=(result,), daemon=True).start()
            except Exception:
                pass

        except Exception as e:
            self.error.emit(f"Inline brain error: {str(e)}")
            self.state_changed.emit("idle")


# ─── Animated Orb Widget (Particle Animation) ───────────────────────────────────
class VoiceOrbWidget(QWidget):
    """The stunning animated voice orb with particle cloud."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 300)
        self._state = "idle"
        self._phase = 0.0
        self._glow_alpha = 0.0
        
        # Initialize particles
        self._num_particles = 150
        self._particles = []
        for i in range(self._num_particles):
            # random spherical coordinates
            theta = random.uniform(0, 2 * math.pi)
            phi = math.acos(random.uniform(-1, 1))
            speed = random.uniform(0.5, 2.0)
            size = random.uniform(2, 6)
            self._particles.append({
                "theta": theta,
                "phi": phi,
                "speed": speed,
                "size": size,
                "base_x": math.sin(phi) * math.cos(theta),
                "base_y": math.sin(phi) * math.sin(theta),
                "base_z": math.cos(phi)
            })

        # Animation timer
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(16)  # ~60fps

    def set_state(self, state: str):
        self._state = state

    def _animate(self):
        # Update phase
        speed = {
            "idle": 0.01, "listening": 0.03, 
            "thinking": 0.05, "talking": 0.07, "awake": 0.04
        }.get(self._state, 0.01)
        self._phase += speed

        # Update glow
        target_glow = {"idle": 0.2, "listening": 0.6, "thinking": 0.8, "talking": 1.0, "awake": 0.7}.get(self._state, 0.2)
        self._glow_alpha += (target_glow - self._glow_alpha) * 0.05

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        
        # Base radius varies slightly by state
        amp = {"idle": 1.0, "listening": 1.1, "thinking": 1.15, "talking": 1.2 + 0.1 * math.sin(self._phase * 5), "awake": 1.05}.get(self._state, 1.0)
        base_r = min(w, h) * 0.22 * amp

        # Define Colors based on state
        if self._state == "talking":
            color_core = QColor(0, 180, 255)
            color_glow = QColor(0, 180, 255, int(80 * self._glow_alpha))
            color_dot1 = QColor(0, 200, 255)
            color_dot2 = QColor(0, 255, 200)
        elif self._state == "thinking":
            color_core = QColor(140, 80, 255)
            color_glow = QColor(140, 80, 255, int(80 * self._glow_alpha))
            color_dot1 = QColor(180, 100, 255)
            color_dot2 = QColor(220, 150, 255)
        elif self._state == "listening":
            color_core = QColor(0, 255, 180)
            color_glow = QColor(0, 255, 180, int(80 * self._glow_alpha))
            color_dot1 = QColor(50, 255, 200)
            color_dot2 = QColor(100, 255, 220)
        elif self._state == "awake":
            color_core = QColor(0, 150, 220)
            color_glow = QColor(0, 150, 220, int(80 * self._glow_alpha))
            color_dot1 = QColor(0, 200, 255)
            color_dot2 = QColor(255, 255, 255)
        else:
            color_core = QColor(30, 80, 140)
            color_glow = QColor(0, 120, 255, int(40 * self._glow_alpha))
            color_dot1 = QColor(80, 150, 255)
            color_dot2 = QColor(100, 200, 255)

        # Background deep glow
        glow = QRadialGradient(QPointF(cx, cy), base_r * 2.5)
        alpha = int(self._glow_alpha * 60)
        glow.setColorAt(0, color_glow)
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        painter.setBrush(QBrush(glow))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy), base_r * 2.5, base_r * 2.5)

        # Core orb shadow
        painter.setBrush(QBrush(QColor(0, 0, 0, 100)))
        painter.drawEllipse(QPointF(cx, cy), base_r * 0.9, base_r * 0.9)

        # Draw particles
        for i, p in enumerate(self._particles):
            # Rotate particle in 3D
            rot_y = self._phase * p["speed"]
            rot_z = self._phase * p["speed"] * 0.5
            
            # Apply rotations
            x1 = p["base_x"] * math.cos(rot_y) - p["base_z"] * math.sin(rot_y)
            z1 = p["base_x"] * math.sin(rot_y) + p["base_z"] * math.cos(rot_y)
            y1 = p["base_y"]
            
            x2 = x1 * math.cos(rot_z) - y1 * math.sin(rot_z)
            y2 = x1 * math.sin(rot_z) + y1 * math.cos(rot_z)
            z2 = z1
            
            # Projection to 2D
            scale = 1.0 + (z2 * 0.3) # Perspective scaling
            px = cx + (x2 * base_r * scale)
            py = cy + (y2 * base_r * scale)

            # Determine behavior by state
            if self._state == "talking":
                px += math.sin(self._phase * 10 + i) * 10 * scale
                py += math.cos(self._phase * 10 + i) * 10 * scale
            elif self._state == "thinking":
                # Swirl
                px += math.sin(rot_y * 3) * 15 * scale
                py += math.cos(rot_y * 3) * 15 * scale

            # Size depth
            dot_size = max(1.0, p["size"] * scale)
            
            # Alpha based on Z-depth
            dot_alpha = max(20, min(255, int(150 + z2 * 100)))
            
            c = color_dot1 if i % 2 == 0 else color_dot2
            c.setAlpha(dot_alpha)

            painter.setBrush(QBrush(c))
            painter.drawEllipse(QPointF(px, py), dot_size / 2, dot_size / 2)

        # State label
        state_labels = {
            "idle": "STANDBY", "listening": "LISTENING",
            "thinking": "PROCESSING", "talking": "SPEAKING", "awake": "AWAKE"
        }
        label = state_labels.get(self._state, "STANDBY")
        font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3)
        painter.setFont(font)
        painter.setPen(QColor(0, 200, 255, 180))
        painter.drawText(
            QRect(0, int(cy + base_r * 1.5 + 20), w, 25),
            Qt.AlignmentFlag.AlignHCenter, label
        )

        painter.end()


# ─── Chat Bubble Widget ───────────────────────────────────────────────────────
class ChatBubble(QFrame):
    def __init__(self, text: str, is_user: bool, parent=None):
        super().__init__(parent)
        self.is_user = is_user
        self.setContentsMargins(0, 4, 0, 4)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)

        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setMaximumWidth(460)

        if is_user:
            font = QFont("Segoe UI", 10)
            label.setFont(font)
            label.setStyleSheet("""
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #1a3a6e, stop:1 #0e2244);
                color: #e0f4ff;
                border-radius: 14px;
                border-top-right-radius: 4px;
                padding: 10px 14px;
                border: 1px solid #1e4488;
            """)
            layout.addStretch()
            layout.addWidget(label)
        else:
            font = QFont("Segoe UI", 10)
            label.setFont(font)
            label.setStyleSheet("""
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0a1428, stop:1 #0f1c38);
                color: #90d8ff;
                border-radius: 14px;
                border-top-left-radius: 4px;
                padding: 10px 14px;
                border: 1px solid #0e3060;
            """)
            # Jarvis icon
            icon_label = QLabel("J")
            icon_label.setFixedSize(28, 28)
            icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_label.setStyleSheet("""
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #0080cc, stop:1 #0040aa);
                color: white;
                border-radius: 14px;
                font-weight: bold;
                font-size: 12px;
            """)
            layout.addWidget(icon_label, alignment=Qt.AlignmentFlag.AlignTop)
            layout.addWidget(label)
            layout.addStretch()

        self.setStyleSheet("background: transparent;")


# ─── Status Bar Widget ────────────────────────────────────────────────────────
class StatusBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)

        # Status dot
        self._dot = QLabel("●")
        self._dot.setFixedWidth(16)
        dot_font = QFont("Segoe UI", 8)
        self._dot.setFont(dot_font)

        self._label = QLabel("Systems Nominal")
        label_font = QFont("Segoe UI", 9)
        self._label.setFont(label_font)
        self._label.setStyleSheet(f"color: {TEXT_SECONDARY.name()};")

        self._model_label = QLabel("● Groq / Llama-3.1-8B")
        model_font = QFont("Segoe UI", 8)
        self._model_label.setFont(model_font)
        self._model_label.setStyleSheet("color: #33aa66;")

        layout.addWidget(self._dot)
        layout.addWidget(self._label)
        layout.addStretch()
        layout.addWidget(self._model_label)

        self.setStyleSheet(f"""
            background: {BG_DARK.name()};
            border-top: 1px solid {BORDER_COLOR.name()};
        """)
        self.set_state("idle")

    def set_state(self, state: str):
        colors = {
            "idle": ("#2288aa", "Standby"),
            "listening": ("#00ff88", "Listening..."),
            "thinking": ("#ffaa00", "Processing..."),
            "talking": ("#00aaff", "Speaking..."),
            "awake": ("#00ffcc", "Awake — Command?"),
            "error": ("#ff4444", "Error"),
        }
        color, text = colors.get(state, ("#2288aa", "Standby"))
        self._dot.setStyleSheet(f"color: {color};")
        self._label.setText(f"  {text}")
        self._label.setStyleSheet(f"color: {color};")


# ─── Control Panel Widget ─────────────────────────────────────────────────────
class ControlPanel(QWidget):
    skill_triggered = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Title
        title = QLabel("⚡  JARVIS CONTROL PANEL")
        title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        title.setStyleSheet("""
            color: #00b4ff;
            letter-spacing: 2px;
            padding: 8px 12px;
            border-bottom: 1px solid #1a3060;
        """)
        layout.addWidget(title)

        # Quick actions grid
        actions = [
            ("🔍 Web Search", "search the web for"),
            ("🎵 YouTube", "play on youtube"),
            ("📸 Screenshot", "take a screenshot"),
            ("📁 Files", "open file explorer"),
            ("🖥️ System Info", "show system info"),
            ("📋 Clipboard", "show clipboard"),
            ("🔊 Volume Up", "volume up"),
            ("🔇 Mute", "mute volume"),
            ("💻 Terminal", "open terminal"),
            ("⚙️ Settings", "open settings"),
            ("📝 Notepad", "open notepad"),
            ("🧹 Clear Chat", "__clear__"),
        ]

        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setSpacing(6)
        grid.setContentsMargins(4, 4, 4, 4)

        for i, (label, cmd) in enumerate(actions):
            btn = QPushButton(label)
            btn.setFont(QFont("Segoe UI", 9))
            btn.setFixedHeight(36)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #0f1c38, stop:1 #080e20);
                    color: #7ab8e8;
                    border: 1px solid #1a3060;
                    border-radius: 8px;
                    padding: 4px 8px;
                    text-align: left;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #162040, stop:1 #0d1830);
                    border-color: #0088cc;
                    color: #00d4ff;
                }
                QPushButton:pressed {
                    background: #0a1428;
                    border-color: #00aaff;
                }
            """)
            btn.clicked.connect(lambda checked, c=cmd: self.skill_triggered.emit(c))
            grid.addWidget(btn, i // 2, i % 2)

        layout.addWidget(grid_widget)

        # Wake word toggle
        wake_frame = QFrame()
        wake_frame.setStyleSheet("""
            background: #080e20;
            border: 1px solid #1a3060;
            border-radius: 8px;
        """)
        wake_layout = QHBoxLayout(wake_frame)
        wake_layout.setContentsMargins(12, 8, 12, 8)

        wake_icon = QLabel("🎙️")
        wake_layout.addWidget(wake_icon)
        wake_label = QLabel("Voice Mode Active")
        wake_label.setFont(QFont("Segoe UI", 9))
        wake_label.setStyleSheet("color: #00ff88; background: transparent;")
        wake_layout.addWidget(wake_label)
        wake_layout.addStretch()

        wake_status = QLabel("Say 'Hey Jarvis'")
        wake_status.setFont(QFont("Segoe UI", 8))
        wake_status.setStyleSheet("color: #507090; background: transparent;")
        wake_layout.addWidget(wake_status)

        layout.addWidget(wake_frame)
        layout.addStretch()


# ─── Main Window ──────────────────────────────────────────────────────────────
class JarvisWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("J.A.R.V.I.S. — Control Interface")
        self.setMinimumSize(1100, 720)
        self.resize(1280, 800)

        # Set Window Icon
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jarvis_icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        # State
        self._jarvis_state = "idle"
        self._is_voice_active = False
        self._is_awake = False
        self._wake_timeout = None
        self._current_response = ""

        # Workers
        self._backend = BackendWorker()
        self._backend.response_chunk.connect(self._on_response_chunk)
        self._backend.state_changed.connect(self._set_state)
        self._backend.error.connect(self._on_error)

        self._voice_worker = VoiceWorker()
        self._voice_worker.transcribed.connect(self._on_transcribed)
        self._voice_worker.state_changed.connect(self._set_state)
        self._voice_worker.error.connect(self._on_error)

        self._setup_ui()
        self._apply_styles()

        # Health check timer
        self._health_timer = QTimer(self)
        self._health_timer.timeout.connect(self._check_backend)
        self._health_timer.start(5000)

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Title Bar ──
        title_bar = self._make_title_bar()
        root_layout.addWidget(title_bar)

        # ── Main Content (split layout) ──
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # LEFT PANEL — Voice Animation
        left_panel = self._make_left_panel()
        content_layout.addWidget(left_panel, stretch=4)

        # Divider
        divider = QFrame()
        divider.setFixedWidth(1)
        divider.setStyleSheet(f"background: {BORDER_COLOR.name()};")
        content_layout.addWidget(divider)

        # RIGHT PANEL — Chat + Control
        right_panel = self._make_right_panel()
        content_layout.addWidget(right_panel, stretch=6)

        root_layout.addWidget(content, stretch=1)

        # ── Status Bar ──
        self._status_bar = StatusBar()
        root_layout.addWidget(self._status_bar)

    def _make_title_bar(self):
        bar = QWidget()
        bar.setFixedHeight(52)
        bar.setStyleSheet(f"""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 {BG_DARK.name()}, stop:0.5 #0a0f1e, stop:1 {BG_DARK.name()});
            border-bottom: 1px solid {BORDER_COLOR.name()};
        """)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 0, 20, 0)

        # Logo + Title
        logo = QLabel("⬡")
        logo.setFont(QFont("Segoe UI", 22))
        logo.setStyleSheet("color: #00b4ff;")
        layout.addWidget(logo)

        title = QLabel("J.A.R.V.I.S.")
        title.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        title.setStyleSheet("""
            color: #00d4ff;
            letter-spacing: 4px;
        """)
        layout.addWidget(title)

        subtitle = QLabel("Just A Rather Very Intelligent System")
        subtitle.setFont(QFont("Segoe UI", 8))
        subtitle.setStyleSheet("color: #305570; margin-left: 12px;")
        layout.addWidget(subtitle)

        layout.addStretch()

        # Backend indicator
        self._backend_indicator = QLabel("● Backend")
        self._backend_indicator.setFont(QFont("Segoe UI", 9))
        self._backend_indicator.setStyleSheet("color: #ff6644;")
        layout.addWidget(self._backend_indicator)

        # Min/Max/Close buttons
        for symbol, tip, callback in [
            ("─", "Minimize", self.showMinimized),
            ("□", "Maximize", self._toggle_maximize),
            ("✕", "Close", self.close),
        ]:
            btn = QPushButton(symbol)
            btn.setFixedSize(40, 32)
            btn.setToolTip(tip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFont(QFont("Segoe UI", 11))
            if symbol == "✕":
                btn.setStyleSheet("""
                    QPushButton { background: transparent; color: #7090a0; border: none; }
                    QPushButton:hover { background: #cc2222; color: white; border-radius: 4px; }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton { background: transparent; color: #7090a0; border: none; }
                    QPushButton:hover { background: #1a3060; color: #00d4ff; border-radius: 4px; }
                """)
            btn.clicked.connect(callback)
            layout.addWidget(btn)

        # Enable drag
        bar._drag_pos = None
        bar.mousePressEvent = lambda e: setattr(bar, '_drag_pos', e.globalPosition().toPoint()) if e.button() == Qt.MouseButton.LeftButton else None
        bar.mouseMoveEvent = lambda e: self.move(self.pos() + e.globalPosition().toPoint() - bar._drag_pos) or setattr(bar, '_drag_pos', e.globalPosition().toPoint()) if bar._drag_pos else None
        bar.mouseReleaseEvent = lambda e: setattr(bar, '_drag_pos', None)

        # Remove native title bar
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)

        return bar

    def _toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _make_left_panel(self):
        panel = QWidget()
        panel.setStyleSheet(f"""
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #05080f, stop:0.5 #080c18, stop:1 #05080f);
        """)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 20, 16, 20)
        layout.setSpacing(16)

        # Orb
        self._orb = VoiceOrbWidget()
        layout.addWidget(self._orb, stretch=3)

        # Voice mode controls
        voice_frame = QFrame()
        voice_frame.setStyleSheet("""
            background: #080e20;
            border: 1px solid #1a3060;
            border-radius: 12px;
        """)
        vf_layout = QVBoxLayout(voice_frame)
        vf_layout.setContentsMargins(16, 14, 16, 14)
        vf_layout.setSpacing(10)

        # Voice toggle button
        self._voice_btn = QPushButton("🎙️  Activate Voice Mode")
        self._voice_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._voice_btn.setFixedHeight(44)
        self._voice_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._voice_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0a2040, stop:1 #081830);
                color: #00d4ff;
                border: 1px solid #0e4080;
                border-radius: 10px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0e2e58, stop:1 #0a2244);
                border-color: #00aaff;
            }
            QPushButton:checked {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #003060, stop:1 #001840);
                border-color: #00ffcc;
                color: #00ffcc;
            }
        """)
        self._voice_btn.setCheckable(True)
        self._voice_btn.toggled.connect(self._toggle_voice_mode)
        vf_layout.addWidget(self._voice_btn)

        # Wake word hint
        hint = QLabel("Say 'Hey Jarvis' or 'Ok Jarvis' to activate")
        hint.setFont(QFont("Segoe UI", 8))
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #304060; background: transparent;")
        vf_layout.addWidget(hint)

        layout.addWidget(voice_frame, stretch=1)

        # System metrics
        metrics_frame = QFrame()
        metrics_frame.setStyleSheet("""
            background: #080e20;
            border: 1px solid #1a3060;
            border-radius: 10px;
        """)
        mf_layout = QHBoxLayout(metrics_frame)
        mf_layout.setContentsMargins(12, 10, 12, 10)

        for label_text, value_text in [("AI MODEL", "Llama 3.1"), ("BACKEND", "FastAPI"), ("STT", "Whisper")]:
            m = QVBoxLayout()
            lbl = QLabel(label_text)
            lbl.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
            lbl.setStyleSheet("color: #304070; letter-spacing: 1px; background: transparent;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val = QLabel(value_text)
            val.setFont(QFont("Segoe UI", 9))
            val.setStyleSheet("color: #00aacc; background: transparent;")
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            m.addWidget(lbl)
            m.addWidget(val)
            mf_layout.addLayout(m)
            if label_text != "STT":
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.VLine)
                sep.setStyleSheet("color: #1a3060;")
                mf_layout.addWidget(sep)

        layout.addWidget(metrics_frame)
        return panel

    def _make_right_panel(self):
        panel = QWidget()
        panel.setStyleSheet(f"background: {BG_PANEL.name()};")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Top: Control Panel ──
        self._control_panel = ControlPanel()
        self._control_panel.setFixedHeight(280)
        self._control_panel.setStyleSheet(f"""
            background: {BG_DARK.name()};
            border-bottom: 1px solid {BORDER_COLOR.name()};
        """)
        self._control_panel.skill_triggered.connect(self._on_quick_action)
        layout.addWidget(self._control_panel)

        # ── Middle: Chat Area ──
        chat_container = QWidget()
        chat_container.setStyleSheet(f"background: {BG_PANEL.name()};")
        chat_layout = QVBoxLayout(chat_container)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(0)

        # Chat header
        chat_header = QWidget()
        chat_header.setFixedHeight(36)
        chat_header.setStyleSheet(f"""
            background: {BG_DARK.name()};
            border-bottom: 1px solid {BORDER_COLOR.name()};
        """)
        ch_layout = QHBoxLayout(chat_header)
        ch_layout.setContentsMargins(16, 0, 16, 0)
        chat_title = QLabel("● CONVERSATION LOG")
        chat_title.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        chat_title.setStyleSheet("color: #304070; letter-spacing: 2px;")
        ch_layout.addWidget(chat_title)
        ch_layout.addStretch()

        clear_btn = QPushButton("Clear")
        clear_btn.setFont(QFont("Segoe UI", 8))
        clear_btn.setFixedHeight(22)
        clear_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #304070;
                border: 1px solid #1a3060;
                border-radius: 4px;
                padding: 0 8px;
            }
            QPushButton:hover { color: #00aaff; border-color: #0088cc; }
        """)
        clear_btn.clicked.connect(self._clear_chat)
        ch_layout.addWidget(clear_btn)
        chat_layout.addWidget(chat_header)

        # Scroll area for messages
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("""
            QScrollArea { 
                border: none; 
                background: transparent;
            }
            QScrollBar:vertical {
                background: #080e20;
                width: 6px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #1a3060;
                border-radius: 3px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: #0088cc;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none; background: none;
            }
        """)

        self._chat_content = QWidget()
        self._chat_content.setStyleSheet(f"background: {BG_PANEL.name()};")
        self._messages_layout = QVBoxLayout(self._chat_content)
        self._messages_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._messages_layout.setContentsMargins(8, 12, 8, 12)
        self._messages_layout.setSpacing(4)
        self._scroll.setWidget(self._chat_content)
        chat_layout.addWidget(self._scroll, stretch=1)

        layout.addWidget(chat_container, stretch=1)

        # ── Bottom: Input Area ──
        input_area = self._make_input_area()
        layout.addWidget(input_area)

        return panel

    def _make_input_area(self):
        area = QWidget()
        area.setFixedHeight(72)
        area.setStyleSheet(f"""
            background: {BG_DARK.name()};
            border-top: 1px solid {BORDER_COLOR.name()};
        """)
        layout = QHBoxLayout(area)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        self._input = QLineEdit()
        self._input.setPlaceholderText("Command Jarvis... or say 'Hey Jarvis' for voice mode")
        self._input.setFont(QFont("Segoe UI", 10))
        self._input.setFixedHeight(44)
        self._input.setStyleSheet("""
            QLineEdit {
                background: #080e20;
                color: #c0d8f0;
                border: 1px solid #1a3060;
                border-radius: 10px;
                padding: 0 16px;
                selection-background-color: #0066aa;
            }
            QLineEdit:focus {
                border-color: #0088cc;
                background: #0a1428;
            }
        """)
        self._input.returnPressed.connect(self._send_message)
        layout.addWidget(self._input, stretch=1)

        send_btn = QPushButton("Send  ⚡")
        send_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        send_btn.setFixedSize(100, 44)
        send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #004488, stop:1 #003366);
                color: #00d4ff;
                border: 1px solid #0066cc;
                border-radius: 10px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0055aa, stop:1 #004488);
                border-color: #00aaff;
            }
            QPushButton:pressed {
                background: #002244;
            }
        """)
        send_btn.clicked.connect(self._send_message)
        layout.addWidget(send_btn)

        return area

    def _apply_styles(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background: {BG_DARK.name()};
                font-family: 'Segoe UI', Arial, sans-serif;
            }}
        """)

    # ─── Logic ────────────────────────────────────────────────────────────────

    def _send_message(self):
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self._add_message(text, is_user=True)
        self._current_response = ""
        self._jarvis_bubble = None
        self._backend.send_message(text)

    def _on_quick_action(self, cmd: str):
        if cmd == "__clear__":
            self._clear_chat()
            return
        self._add_message(f"[Quick Action] {cmd}", is_user=True)
        self._current_response = ""
        self._backend.send_message(cmd)

    def _add_message(self, text: str, is_user: bool):
        bubble = ChatBubble(text, is_user)
        self._messages_layout.addWidget(bubble)
        # Scroll to bottom
        QTimer.singleShot(50, lambda: self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()
        ))
        return bubble

    def _on_response_chunk(self, text: str, done: bool):
        if not self._current_response:
            # Create new bubble
            self._jarvis_bubble = self._add_message(text, is_user=False)
            self._current_response = text
        else:
            # Update existing bubble
            if hasattr(self, '_jarvis_bubble') and self._jarvis_bubble:
                # Find the label in the bubble and update
                for child in self._jarvis_bubble.findChildren(QLabel):
                    if child.wordWrap():
                        child.setText(text)
                        self._current_response = text
                        break

        if done:
            self._current_response = ""
            self._jarvis_bubble = None
            self._set_state("idle")

    def _clear_chat(self):
        while self._messages_layout.count():
            item = self._messages_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Show welcome message
        QTimer.singleShot(100, lambda: self._add_message(
            "Good day, sir. J.A.R.V.I.S. reporting for duty. All systems operational. How may I assist you?",
            is_user=False
        ))

    def _toggle_voice_mode(self, enabled: bool):
        self._is_voice_active = enabled
        if enabled:
            self._voice_btn.setText("🔴  Voice Mode Active — Listening...")
            self._voice_worker.start_listening()
            self._set_state("listening")
            self._add_message(
                "Voice mode activated. Continuous listening enabled. Say 'Hey Jarvis' to start a command, 'Go to sleep' to deactivate voice.",
                is_user=False
            )
        else:
            self._voice_btn.setText("🎙️  Activate Voice Mode")
            self._voice_worker.stop_listening()
            self._is_awake = False
            self._set_state("idle")
            self._add_message("Voice mode deactivated.", is_user=False)

    def _on_transcribed(self, text: str):
        text_lower = text.lower().strip()

        # Filter noise
        if len(text_lower) < 3:
            return

        noise_words = {"thank you", "thanks", "you", "the", "bye", "okay", "uh", "um", "hmm", "yeah"}
        if text_lower in noise_words:
            return

        # Passive mode (Idle)
        if not self._is_awake:
            found_wake = any(w in text_lower for w in WAKE_WORDS)
            if not found_wake:
                return

            self._is_awake = True
            self._set_state("awake")
            self._active_buffer = ""

            # Strip wake word to see if a command is already attached
            for w in sorted(WAKE_WORDS, key=len, reverse=True):
                text_lower = text_lower.replace(w, "").strip()

            if not text_lower:
                responses = [
                    "At your service, sir.", "Online and ready.", "I'm here.",
                    "Systems active. Go ahead.", "Standing by for your command."
                ]
                resp = random.choice(responses)
                self._add_message(resp, is_user=False)
                try:
                    from tts.pocket_tts import speak
                    threading.Thread(target=speak, args=(resp,), daemon=True).start()
                except Exception:
                    pass
                self._reset_wake_timeout()  # Wait 6.5s for instructions
                return

            # If there's already a command after the wake word, treat it as the text
            text = text_lower

        # Active Mode — appending instructions
        sleep_phrases = ["go to sleep", "that's all", "nevermind", "stop listening", "goodbye"]
        if any(p in text_lower for p in sleep_phrases):
            self._go_to_sleep(reason="Sleep phrase detected.")
            return

        if not text.strip():
            return

        # Add to the buffer and restart the patience timer
        if not hasattr(self, "_active_buffer"):
            self._active_buffer = ""
        self._active_buffer += " " + text
        self._active_buffer = self._active_buffer.strip()

        # Visual feedback
        self._set_state("listening")
        self._reset_wake_timeout()

    def _reset_wake_timeout(self):
        if self._wake_timeout:
            self._wake_timeout.stop()
        self._wake_timeout = QTimer()
        self._wake_timeout.setSingleShot(True)
        # Wait 6.5 seconds after the user stops speaking before executing
        self._wake_timeout.timeout.connect(self._execute_buffer)
        self._wake_timeout.start(6500)

    def _execute_buffer(self):
        if not self._is_awake:
            return
            
        buffer = getattr(self, "_active_buffer", "").strip()
        if buffer:
            # We have accumulated instructions. Execute them!
            self._add_message(f"🎙️ {buffer}", is_user=True)
            self._current_response = ""
            self._backend.send_message(buffer)
            self._active_buffer = ""
            
            # After sending the message, we wait for backend response
            # Note: After task completion, BackendWorker will reset state to 'idle'.
            self._is_awake = False
        else:
            # No instructions received, timed out. Go back to sleep.
            self._go_to_sleep()

    def _go_to_sleep(self, reason=None):
        if self._is_awake:
            self._is_awake = False
            self._set_state("idle")
            self._active_buffer = ""
            resp = reason if reason else "Going to sleep. Say my name to wake me."
            self._add_message(resp, is_user=False)
            try:
                from tts.pocket_tts import speak
                threading.Thread(target=speak, args=(resp,), daemon=True).start()
            except Exception:
                pass

    def _set_state(self, state: str):
        self._jarvis_state = state
        self._orb.set_state(state)
        self._status_bar.set_state(state)

    def _on_error(self, error: str):
        self._add_message(f"⚠️ {error}", is_user=False)
        self._set_state("idle")

    def _check_backend(self):
        def _check():
            try:
                resp = requests.get(f"{BACKEND_URL}/health", timeout=2)
                if resp.status_code == 200:
                    return True
            except Exception:
                pass
            return False

        def _update(ok: bool):
            if ok:
                self._backend_indicator.setText("● Backend")
                self._backend_indicator.setStyleSheet("color: #00cc66;")
            else:
                self._backend_indicator.setText("● Backend Offline")
                self._backend_indicator.setStyleSheet("color: #ff6644;")

        def _run():
            ok = _check()
            # Use QTimer to update UI from main thread
            QTimer.singleShot(0, lambda: _update(ok))

        threading.Thread(target=_run, daemon=True).start()

    def showEvent(self, event):
        super().showEvent(event)
        # Welcome message after a short delay
        QTimer.singleShot(500, lambda: self._add_message(
            "Good day, sir. J.A.R.V.I.S. reporting for duty. All systems operational. "
            "How may I assist you today?",
            is_user=False
        ))


# ─── Entry Point ──────────────────────────────────────────────────────────────
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("J.A.R.V.I.S.")
    app.setOrganizationName("Stark Industries")

    window = JarvisWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
