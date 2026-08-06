"""
websocket_manager.py — WebSocket Connection Manager for JARVIS
==============================================================
Manages active WebSocket connections and broadcasts state/chat/JSON events.
"""

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_state(self, state: str):
        for ws in self.active_connections:
            try:
                await ws.send_json({"state": state})
            except Exception:
                pass

    async def broadcast_chat(self, text: str, role: str = "assistant"):
        for ws in self.active_connections:
            try:
                await ws.send_json({"type": "chat", "text": text, "role": role})
            except Exception:
                pass

    async def broadcast_json(self, payload: dict):
        for ws in self.active_connections:
            try:
                await ws.send_json(payload)
            except Exception:
                pass

    async def close_all(self):
        for ws in list(self.active_connections):
            try:
                await ws.close()
            except Exception:
                pass
        self.active_connections.clear()


manager = ConnectionManager()
