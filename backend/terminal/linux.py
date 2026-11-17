"""POSIX PTY backend (Linux, BSD, macOS) using the stdlib pty module."""
from __future__ import annotations

import errno
import os
import pty
import signal
import struct
import fcntl
import termios
import threading

from .base import TerminalProcess


class LinuxPty(TerminalProcess):
    def __init__(self, argv: list[str], cwd: str | None, env: dict[str, str],
                 rows: int, cols: int, preexec_limits=None):
        super().__init__()
        self._fd: int | None = None
        pid, fd = pty.fork()
        if pid == 0:  # child
            try:
                if cwd:
                    os.chdir(cwd)
                if preexec_limits is not None:
                    preexec_limits()
                fcntl.ioctl(0, termios.TIOCSWINSZ,
                            struct.pack("HHHH", max(rows, 1), max(cols, 1), 0, 0))
                os.execve(argv[0], argv, env)
            except Exception:
                os._exit(127)
        # parent
        self._pid = pid
        self._fd = fd
        self._reader = threading.Thread(target=self._read_loop, name="pty-reader",
                                        daemon=True)
        self._reader.start()

    @property
    def pid(self) -> int | None:
        return self._pid

    def write(self, data: bytes) -> None:
        if self._fd is None:
            return
        try:
            os.write(self._fd, data)
        except OSError as exc:
            if exc.errno not in (errno.EIO, errno.EBADF):
                pass
            self._mark_dead()

    def resize(self, rows: int, cols: int) -> None:
        if self._fd is None:
            return
        try:
            fcntl.ioctl(self._fd, termios.TIOCSWINSZ,
                        struct.pack("HHHH", max(rows, 1), max(cols, 1), 0, 0))
        except OSError:
            pass

    def terminate(self) -> None:
        if self._pid:
            for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(self._pid, sig)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        os.kill(self._pid, sig)
                    except OSError:
                        break
                try:
                    os.waitpid(self._pid, os.WNOHANG)
                except OSError:
                    pass
                sig_done = False
                # brief grace between signals handled by caller retry; keep it simple
                if sig == signal.SIGHUP:
                    import time as _time
                    _time.sleep(0.15)
                if sig_done:
                    break
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        self._mark_dead()

    def _read_loop(self) -> None:
        while self._fd is not None:
            try:
                chunk = os.read(self._fd, 65536)
            except OSError as exc:
                if exc.errno == errno.EIO:  # child exited, slave side closed
                    break
                if exc.errno == errno.EBADF:
                    break
                if exc.errno == errno.EAGAIN:
                    continue
                break
            except Exception:
                break
            if not chunk:
                break
            self.output_queue.put(chunk)
        # reap child
        if self._pid:
            try:
                os.waitpid(self._pid, os.WNOHANG)
            except OSError:
                pass
        self._mark_dead()
