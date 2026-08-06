"""
event_bus.py — Async Event Bus for JARVIS
=========================================
FIFO queue for BusEvent objects. Decouples voice detection from command processing.
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(Enum):
    WAKE = "wake"
    STOP = "stop"
    COMMAND = "command"
    MUTE = "mute"


@dataclass
class BusEvent:
    event_type: EventType
    payload: dict = field(default_factory=dict)


class EventBus:
    def __init__(self):
        self._queue: asyncio.Queue[BusEvent] = asyncio.Queue()

    async def emit(self, event: BusEvent):
        await self._queue.put(event)

    async def next_event(self) -> BusEvent:
        return await self._queue.get()

    def has_events(self) -> bool:
        return not self._queue.empty()


event_bus = EventBus()
