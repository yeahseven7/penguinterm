"""Unit tests for the SQLite layer (uses a temp database)."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from backend.db import Database


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


def test_user_lifecycle(db):
    uid = db.create_user("alice", "hash", "salt", role="editor")
    user = db.get_user(uid)
    assert user["username"] == "alice" and user["role"] == "editor"
    db.update_user(uid, last_seen=time.time())
    assert db.get_user(uid)["last_seen"] is not None
    db.delete_user(uid)
    assert db.get_user(uid) is None


def test_owner_cannot_be_deleted(db):
    uid = db.create_user("boss", "hash", "salt", role="owner")
    db.delete_user(uid)
    assert db.get_user(uid) is not None


def test_tokens(db):
    uid = db.create_user("tok", "hash", "salt", role="viewer")
    db.store_token("abc123", uid, 60)
    assert db.token_user("abc123")["id"] == uid
    db.revoke_token("abc123")
    assert db.token_user("abc123") is None


def test_expired_token_rejected(db):
    uid = db.create_user("exp", "hash", "salt", role="viewer")
    db.store_token("expired", uid, -1)  # already expired
    assert db.token_user("expired") is None


def test_transfer_records(db):
    tid = db.record_transfer("receive", "peer", "file.bin", 123, "deadbeef", "done", "/tmp/x")
    rows = db.list_transfers()
    assert rows[0]["id"] == tid and rows[0]["filename"] == "file.bin"
    db.execute("DELETE FROM transfers WHERE id = ?", (tid,))
    assert db.list_transfers() == []


def test_shared_dirs(db):
    db.add_shared_dir("/srv/shared", "team")
    dirs = db.list_shared_dirs()
    assert dirs and dirs[0]["path"] == "/srv/shared"
    db.remove_shared_dir(dirs[0]["id"])
    assert db.list_shared_dirs() == []


def test_invites(db):
    db.create_invite("tok-invite", "editor", None, 1, None, None)
    invite = db.get_invite("tok-invite")
    assert invite["role"] == "editor" and invite["uses"] == 0
    db.consume_invite("tok-invite")
    assert db.get_invite("tok-invite")["uses"] == 1
