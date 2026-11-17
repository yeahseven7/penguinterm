"""A tiny pub/sub bus used to fan out server events (/ws/events).

`publish()` is safe to call from any thread; frames are delivered on the
asyncio loop that was bound at startup via `bind_loop()`. If no loop is
bound (e.g. unit tests), frames are dropped silently.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any


class EventsBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, event: str, payload: dict[str, Any] | None = None) -> None:
        loop = self._loop
        message = {"event": event, "payload": payload or {}, "ts": time.time()}
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
        try:
            loop.call_soon_threadsafe(self._deliver, message)
        except RuntimeError:
            pass  # loop closed

    def _deliver(self, message: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                pass


events_bus = EventsBus()
