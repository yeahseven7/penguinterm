"""Windows ConPTY backend (pywinpty)."""
from __future__ import annotations

import threading

from .base import TerminalProcess


class WindowsConPty(TerminalProcess):
    def __init__(self, argv: list[str], cwd: str | None, env: dict[str, str],
                 rows: int, cols: int):
        super().__init__()
        from winpty import PtyProcess  # imported lazily; only present on Windows

        self._proc = PtyProcess.spawn(
            argv,
            cwd=cwd,
            env=env,
            dimensions=(max(rows, 1), max(cols, 1)),
        )
        self._reader = threading.Thread(target=self._read_loop, name="conpty-reader",
                                        daemon=True)
        self._reader.start()

    @property
    def pid(self) -> int | None:
        try:
            return int(self._proc.pid)
        except Exception:
            return None

    def write(self, data: bytes) -> None:
        try:
            self._proc.write(data.decode("utf-8", errors="ignore"))
        except Exception:
            self._mark_dead()

    def resize(self, rows: int, cols: int) -> None:
        try:
            self._proc.setwinsize(max(rows, 1), max(cols, 1))
        except Exception:
            pass

    def terminate(self) -> None:
        try:
            self._proc.terminate()
        except Exception:
            pass
        try:
            self._proc.close()
        except Exception:
            pass
        self._mark_dead()

    def _read_loop(self) -> None:
        while True:
            try:
                chunk = self._proc.read(65536)
            except EOFError:
                break
            except Exception:
                break
            if chunk is None:
                break
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8", errors="ignore")
            if chunk:
                self.output_queue.put(chunk)
            else:
                break
        self._mark_dead()
