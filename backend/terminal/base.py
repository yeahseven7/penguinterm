"""Common interface for platform-specific PTY/ConPTY processes."""
from __future__ import annotations

import abc
import queue
import threading


class TerminalProcess(abc.ABC):
    """A real shell process attached to a real (pseudo)terminal.

    Reads arrive on a background thread and are pushed into `output_queue`.
    A None item signals end of stream.
    """

    def __init__(self) -> None:
        self.output_queue: queue.Queue[bytes | None] = queue.Queue()
        self._alive = True
        self._lock = threading.Lock()

    @property
    @abc.abstractmethod
    def pid(self) -> int | None: ...

    @abc.abstractmethod
    def write(self, data: bytes) -> None: ...

    @abc.abstractmethod
    def resize(self, rows: int, cols: int) -> None: ...

    @abc.abstractmethod
    def terminate(self) -> None: ...

    def is_alive(self) -> bool:
        with self._lock:
            return self._alive

    def _mark_dead(self) -> None:
        with self._lock:
            self._alive = False
        self.output_queue.put(None)
