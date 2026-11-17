"""Terminal session registry: local (private) and shared sessions.

Responsibilities:
  * spawn real shell processes (ConPTY / PTY) with the right cwd, env, limits
  * fan output out to every attached viewer with a scrollback replay buffer
  * gate input by role + session input mode
  * reap dead sessions and enforce limits

Threading model: one daemon thread per session pumps bytes from the OS-level
reader queue into the scrollback ring and into each subscriber's asyncio
queue (delivery is marshalled onto the bound asyncio loop). Websocket
handlers consume those queues. No event loop is required at spawn time.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config
from ..db import Database
from ..events import events_bus
from .. import security
from ..shells import ShellInfo, detect_shells
from .base import TerminalProcess

if sys.platform == "win32":
    from .windows import WindowsConPty as _PlatformTerminal
else:
    from .linux import LinuxPty as _PlatformTerminal


@dataclass
class Subscriber:
    user_id: int
    username: str
    role: str
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=2000))
    ctrl_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=200))
    _key: str | None = None


@dataclass
class Session:
    id: str
    title: str
    shell_id: str
    shell_name: str
    shell_kind: str
    shell_path: str
    kind: str                  # 'local' | 'shared'
    created_by: int
    created_by_name: str
    restricted: bool
    input_mode: str            # 'all' | 'owner'  (shared sessions)
    created_at: float
    cols: int
    rows: int
    process: TerminalProcess
    scrollback: bytearray = field(default_factory=bytearray)
    total_output_bytes: int = 0
    subscribers: dict[str, Subscriber] = field(default_factory=dict)
    exited: bool = False
    exit_code: int | None = None
    ended_at: float | None = None
    last_activity: float = field(default_factory=time.time)
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _scrollback_lock: threading.Lock = field(default_factory=threading.Lock)
    _job_handle: Any = None


def _shell_by_id(shell_id: str) -> ShellInfo | None:
    for shell in detect_shells():
        if shell["id"] == shell_id:
            return shell
    return None


class SessionManager:
    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db
        self.sessions: dict[str, Session] = {}
        self._max_buffer = int(cfg.get("max_output_buffer", 2_000_000))
        security.ensure_shims(cfg.shim_dir)

    # ------------------------------------------------------------------
    # creation
    # ------------------------------------------------------------------
    def create(self, *, shell_id: str | None, title: str | None,
               kind: str, user: dict[str, Any],
               input_mode: str = "all", cols: int = 120, rows: int = 30,
               restricted: bool | None = None) -> Session:
        if kind == "shared" and user["role"] != "owner":
            raise PermissionError("Only the server owner can open a shared session")
        if kind == "local" and user["role"] != "owner":
            allow = bool(self.cfg.get("remote_create_sessions", False))
            if not (allow and user["role"] == "editor"):
                raise PermissionError("Remote users may not open local sessions on this host")

        active = [s for s in self.sessions.values() if not s.exited]
        if len(active) >= int(self.cfg.get("max_sessions", 16)):
            raise RuntimeError("Session limit reached")

        shell = _shell_by_id(shell_id) if shell_id else None
        if shell is None:
            shells = detect_shells()
            from ..shells import default_shell
            fallback = default_shell(shells)
            shell = next((s for s in shells if s["id"] == fallback), None)
        if shell is None:
            raise RuntimeError("No shell detected on this system")

        is_remote = int(user["id"]) != self._owner_user_id()
        if restricted is None:
            restricted = bool(self.cfg.get("restricted_mode", True)) and (
                is_remote
                or user["role"] != "owner"
                or not bool(self.cfg.get("allow_owner_full_shell", True))
            )

        env = self._build_env(restricted=restricted)
        cwd = self._start_cwd(restricted)
        argv = [shell["path"], *shell["args"]]

        if sys.platform == "win32":
            proc = _PlatformTerminal(argv, cwd=cwd, env=env,
                                      rows=max(rows, 1), cols=max(cols, 1))
        else:
            proc = _PlatformTerminal(argv, cwd=cwd, env=env,
                                      rows=max(rows, 1), cols=max(cols, 1),
                                      preexec_limits=(
                                          security.apply_linux_limits if restricted else None))

        session_id = uuid.uuid4().hex[:12]
        session = Session(
            id=session_id,
            title=title or shell["name"],
            shell_id=shell["id"],
            shell_name=shell["name"],
            shell_kind=shell["kind"],
            shell_path=shell["path"],
            kind=kind,
            created_by=int(user["id"]),
            created_by_name=user["username"],
            restricted=restricted,
            input_mode=input_mode if input_mode in ("all", "owner") else "all",
            created_at=time.time(),
            cols=max(cols, 1), rows=max(rows, 1),
            process=proc,
        )
        if sys.platform == "win32" and restricted and proc.pid:
            session._job_handle = security.apply_windows_job_object(proc.pid)

        self.sessions[session_id] = session
        self._spawn_pump(session)

        self.db.log("INFO", f"session {session_id} created ({kind}, shell={shell['id']}, "
                            f"restricted={restricted}, by {user['username']})", "terminal")
        events_bus.publish("session_created", self.describe(session, for_user=None))
        return session

    def _owner_user_id(self) -> int:
        row = self.db.query_one("SELECT id FROM users WHERE role='owner' ORDER BY id LIMIT 1")
        return int(row["id"]) if row else -1

    def _build_env(self, *, restricted: bool) -> dict[str, str]:
        if restricted:
            env = security.scrub_env(extra_path=str(self.cfg.shim_dir), restricted=True)
        else:
            env = dict(os.environ)
            env["PENGUINTERM_RESTRICTED"] = "0"
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env.setdefault("LANG", "C.UTF-8")
        return env

    def _start_cwd(self, restricted: bool) -> str:
        if restricted:
            return security.remote_start_cwd(self.cfg.get)
        return str(Path.home())

    # ------------------------------------------------------------------
    # output pumping (one daemon thread per session)
    # ------------------------------------------------------------------
    def _spawn_pump(self, session: Session) -> None:
        src = session.process.output_queue

        def pump() -> None:
            while True:
                try:
                    item = src.get()
                except Exception:
                    item = None
                if item is None:
                    self._handle_exit(session)
                    return
                with session._scrollback_lock:
                    session.scrollback.extend(item)
                    overflow = len(session.scrollback) - self._max_buffer
                    if overflow > 0:
                        del session.scrollback[:overflow]
                    session.total_output_bytes += len(item)
                    session.last_activity = time.time()
                    for key, sub in list(session.subscribers.items()):
                        self._offer(session, key, sub, item)

        threading.Thread(target=pump, name=f"pump-{session.id}", daemon=True).start()

    def _offer(self, session: Session, key: str, sub: Subscriber,
               item: bytes | None) -> None:
        loop = events_bus.loop  # bound at app startup
        if loop is not None:
            try:
                loop.call_soon_threadsafe(self._deliver, session, key, sub, item)
                return
            except RuntimeError:
                pass
        # No loop (tests): direct best-effort put
        self._deliver(session, key, sub, item)

    def _deliver(self, session: Session, key: str, sub: Subscriber,
                 item: bytes | None) -> None:
        try:
            sub.queue.put_nowait(item)
        except asyncio.QueueFull:
            if item is None:
                return
            # Drop the oldest frame to make room; terminals prefer fresh state.
            try:
                sub.queue.get_nowait()
                sub.queue.put_nowait(item)
            except Exception:
                pass

    def _handle_exit(self, session: Session) -> None:
        if session.exited:
            return
        session.exited = True
        session.ended_at = time.time()
        self.db.log("INFO", f"session {session.id} exited", "terminal")
        events_bus.publish("session_exited", {"id": session.id})
        for key, sub in list(session.subscribers.items()):
            self._offer(session, key, sub, None)

    # ------------------------------------------------------------------
    # attach / detach / IO
    # ------------------------------------------------------------------
    def can_view(self, session: Session, user: dict[str, Any]) -> bool:
        if session.kind == "local":
            return int(user["id"]) == session.created_by
        return user.get("status") == "active"

    def can_input(self, session: Session, user: dict[str, Any]) -> bool:
        if session.exited:
            return False
        if session.kind == "local":
            return int(user["id"]) == session.created_by
        if user["role"] == "owner":
            return True
        if user["role"] != "editor":
            return False  # viewers never type
        return session.input_mode == "all"

    def attach(self, session: Session, user: dict[str, Any],
               max_bytes: int | None = None) -> tuple[Subscriber, bytes]:
        if not self.can_view(session, user):
            raise PermissionError("Not allowed to view this session")
        sub = Subscriber(user_id=int(user["id"]), username=user["username"], role=user["role"])
        sub_id = uuid.uuid4().hex
        sub._key = sub_id
        with session._scrollback_lock:
            session.subscribers[sub_id] = sub
            replay = bytes(session.scrollback)
        if max_bytes:
            replay = replay[-max_bytes:]
        return sub, replay

    def detach(self, session: Session, sub: Subscriber) -> None:
        if sub._key is not None:
            session.subscribers.pop(sub._key, None)

    def broadcast_viewers(self, session: Session) -> None:
        viewers = [{"username": s.username, "role": s.role}
                   for s in session.subscribers.values()]
        payload = {"type": "viewers", "session_id": session.id, "viewers": viewers}
        for sub in list(session.subscribers.values()):
            try:
                sub.ctrl_queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    def send_ctrl(self, session: Session, sub: Subscriber, payload: dict[str, Any]) -> None:
        try:
            sub.ctrl_queue.put_nowait(payload)
        except asyncio.QueueFull:
            pass

    async def write(self, session: Session, user: dict[str, Any], data: bytes) -> bool:
        if not self.can_input(session, user):
            return False
        # Defense-in-depth: log risky command lines (not a boundary).
        try:
            risk = security.classify_command(data.decode("utf-8", errors="ignore"))
            if risk:
                self.db.log("WARN", f"risky input in session {session.id} from "
                                    f"{user['username']}: pattern {risk}", "security")
        except Exception:
            pass
        async with session.write_lock:
            session.process.write(data)
        return True

    def resize(self, session: Session, cols: int, rows: int) -> None:
        session.cols, session.rows = max(cols, 1), max(rows, 1)
        session.process.resize(session.rows, session.cols)

    def close(self, session: Session, user: dict[str, Any] | None = None) -> bool:
        if user is not None and int(user["id"]) != session.created_by and user["role"] != "owner":
            return False
        try:
            session.process.terminate()
        except Exception:
            pass
        self.sessions.pop(session.id, None)
        for sub in list(session.subscribers.values()):
            try:
                sub.queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        events_bus.publish("session_closed", {"id": session.id})
        return True

    # ------------------------------------------------------------------
    # introspection
    # ------------------------------------------------------------------
    def describe(self, session: Session, for_user: dict[str, Any] | None) -> dict[str, Any]:
        viewers = [{"username": s.username, "role": s.role}
                   for s in session.subscribers.values()]
        return {
            "id": session.id,
            "title": session.title,
            "shell_id": session.shell_id,
            "shell_name": session.shell_name,
            "shell_kind": session.shell_kind,
            "kind": session.kind,
            "created_by": session.created_by_name,
            "restricted": session.restricted,
            "input_mode": session.input_mode,
            "created_at": session.created_at,
            "cols": session.cols,
            "rows": session.rows,
            "exited": session.exited,
            "viewer_count": len(viewers),
            "viewers": viewers,
            "host_os": sys.platform,
            "can_input": (self.can_input(session, for_user) if for_user else False),
        }

    def list_for(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        return [self.describe(s, user) for s in self.sessions.values()
                if self.can_view(s, user)]

    def get(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    def shutdown_all(self) -> None:
        for session in list(self.sessions.values()):
            try:
                session.process.terminate()
            except Exception:
                pass
        self.sessions.clear()
