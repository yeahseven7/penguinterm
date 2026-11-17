"""Session manager tests against a real shell (ConPTY on Windows, PTY on Linux)."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from backend.config import Config
from backend.db import Database
from backend.terminal import SessionManager
from backend.shells import detect_shells, default_shell

OWNER = {"id": 1, "username": "testowner", "role": "owner", "status": "active"}
EDITOR = {"id": 2, "username": "testeditor", "role": "editor", "status": "active"}
VIEWER = {"id": 3, "username": "testviewer", "role": "viewer", "status": "active"}


@pytest.fixture()
def manager(tmp_path):
    cfg = Config(tmp_path / "config.json")
    db = Database(tmp_path / "test.db")
    mgr = SessionManager(cfg, db)
    yield mgr
    mgr.shutdown_all()
    db.close()


def _wait_for_bytes(session, marker: bytes, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if marker in session.scrollback:
            return True
        if session.exited:
            return False
        time.sleep(0.1)
    return marker in session.scrollback


def test_spawn_and_echo(manager):
    shell_id = default_shell(detect_shells())
    if shell_id is None:
        pytest.skip("no shell detected on this platform")
    session = manager.create(shell_id=shell_id, title="t", kind="local", user=OWNER)
    marker = f"PENGUINTERM_MARKER_{int(time.time())}"
    time.sleep(1.0)  # let the shell boot
    session.process.write(f"echo {marker}\r\n".encode())
    assert _wait_for_bytes(session, marker.encode()), "shell output never arrived"
    assert manager.close(session, OWNER)


def test_shared_session_input_rules(manager):
    shell_id = default_shell(detect_shells())
    if shell_id is None:
        pytest.skip("no shell detected on this platform")
    session = manager.create(shell_id=shell_id, title="s", kind="shared",
                             user=OWNER, input_mode="all")
    assert manager.can_input(session, OWNER)
    assert manager.can_input(session, EDITOR)
    assert not manager.can_input(session, VIEWER)

    session.input_mode = "owner"
    assert manager.can_input(session, OWNER)
    assert not manager.can_input(session, EDITOR)

    manager.close(session, OWNER)


def test_local_session_invisible_to_others(manager):
    shell_id = default_shell(detect_shells())
    if shell_id is None:
        pytest.skip("no shell detected on this platform")
    session = manager.create(shell_id=shell_id, title="private", kind="local", user=OWNER)
    assert manager.can_view(session, OWNER)
    assert not manager.can_view(session, EDITOR)
    assert not manager.can_view(session, VIEWER)

    with pytest.raises(PermissionError):
        manager.attach(session, VIEWER)

    manager.close(session, OWNER)


def test_max_sessions_enforced(manager, tmp_path):
    manager.cfg.set("max_sessions", 1)
    shell_id = default_shell(detect_shells())
    if shell_id is None:
        pytest.skip("no shell detected on this platform")
    first = manager.create(shell_id=shell_id, title="1", kind="local", user=OWNER)
    with pytest.raises(RuntimeError):
        manager.create(shell_id=shell_id, title="2", kind="local", user=OWNER)
    manager.close(first, OWNER)
