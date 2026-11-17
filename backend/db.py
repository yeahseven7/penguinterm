"""SQLite persistence for PenguinTerm.

A single file database in the data directory. WAL mode, a single shared
connection guarded by a lock (terminal throughput is fanned out over
WebSockets, not the database).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    salt          TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'viewer' CHECK(role IN ('owner','editor','viewer')),
    status        TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','pending','blocked')),
    created_at    REAL NOT NULL,
    last_seen     REAL
);
CREATE TABLE IF NOT EXISTS auth_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    label      TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS rooms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    code       TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    created_by INTEGER REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS room_members (
    room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role    TEXT NOT NULL DEFAULT 'viewer',
    PRIMARY KEY (room_id, user_id)
);
CREATE TABLE IF NOT EXISTS invites (
    token      TEXT PRIMARY KEY,
    role       TEXT NOT NULL DEFAULT 'viewer' CHECK(role IN ('owner','editor','viewer')),
    room_id    INTEGER REFERENCES rooms(id) ON DELETE CASCADE,
    max_uses   INTEGER NOT NULL DEFAULT 1,
    uses       INTEGER NOT NULL DEFAULT 0,
    expires_at REAL,
    created_by INTEGER REFERENCES users(id),
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS saved_servers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT NOT NULL,
    host        TEXT NOT NULL,
    port        INTEGER NOT NULL DEFAULT 8443,
    tls         INTEGER NOT NULL DEFAULT 1,
    username    TEXT NOT NULL DEFAULT '',
    os_hint     TEXT DEFAULT '',
    added_at    REAL NOT NULL,
    last_seen   REAL,
    last_status TEXT DEFAULT 'unknown'
);
CREATE TABLE IF NOT EXISTS transfers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    direction   TEXT NOT NULL CHECK(direction IN ('send','receive')),
    peer        TEXT NOT NULL DEFAULT '',
    filename    TEXT NOT NULL,
    size        INTEGER NOT NULL DEFAULT 0,
    sha256      TEXT DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',
    path        TEXT DEFAULT '',
    created_at  REAL NOT NULL,
    completed_at REAL
);
CREATE TABLE IF NOT EXISTS logs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    level   TEXT NOT NULL DEFAULT 'INFO',
    source  TEXT NOT NULL DEFAULT 'server',
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shared_dirs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    path       TEXT NOT NULL UNIQUE,
    label      TEXT DEFAULT '',
    added_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts DESC);
CREATE INDEX IF NOT EXISTS idx_transfers_created ON transfers(created_at DESC);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    def query_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    def log(self, level: str, message: str, source: str = "server") -> None:
        try:
            self.execute(
                "INSERT INTO logs(ts, level, source, message) VALUES (?,?,?,?)",
                (time.time(), level.upper(), source, message[:2000]),
            )
        except sqlite3.Error:
            pass  # logging must never crash the server

    def recent_logs(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.query(
            "SELECT * FROM logs ORDER BY ts DESC LIMIT ?", (min(int(limit), 1000),)
        )

    # users -------------------------------------------------------------
    def create_user(self, username: str, password_hash: str, salt: str,
                    role: str = "viewer", status: str = "active") -> int:
        cur = self.execute(
            "INSERT INTO users(username, password_hash, salt, role, status, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (username, password_hash, salt, role, status, time.time()),
        )
        return int(cur.lastrowid)

    def get_user_by_name(self, username: str) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM users WHERE username = ?", (username,))

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM users WHERE id = ?", (user_id,))

    def list_users(self) -> list[dict[str, Any]]:
        return self.query(
            "SELECT id, username, role, status, created_at, last_seen FROM users"
            " ORDER BY CASE role WHEN 'owner' THEN 0 ELSE 1 END, username"
        )

    def update_user(self, user_id: int, **fields: Any) -> None:
        allowed = {"role", "status", "last_seen", "password_hash", "salt"}
        sets = [f"{k} = ?" for k in fields if k in allowed]
        vals = [fields[k] for k in fields if k in allowed]
        if not sets:
            return
        vals.append(user_id)
        self.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", tuple(vals))

    def delete_user(self, user_id: int) -> None:
        self.execute("DELETE FROM users WHERE id = ? AND role != 'owner'", (user_id,))

    # tokens --------------------------------------------------------------
    def store_token(self, token_hash: str, user_id: int, ttl_seconds: int, label: str = "") -> None:
        now = time.time()
        self.execute(
            "INSERT OR REPLACE INTO auth_tokens(token_hash, user_id, created_at, expires_at, label)"
            " VALUES (?,?,?,?,?)",
            (token_hash, user_id, now, now + ttl_seconds, label),
        )

    def token_user(self, token_hash: str) -> dict[str, Any] | None:
        row = self.query_one(
            "SELECT u.* FROM auth_tokens t JOIN users u ON u.id = t.user_id"
            " WHERE t.token_hash = ? AND t.expires_at > ?",
            (token_hash, time.time()),
        )
        return row

    def revoke_token(self, token_hash: str) -> None:
        self.execute("DELETE FROM auth_tokens WHERE token_hash = ?", (token_hash,))

    def purge_expired_tokens(self) -> None:
        self.execute("DELETE FROM auth_tokens WHERE expires_at <= ?", (time.time(),))

    # saved servers (friends) ----------------------------------------------
    def add_server(self, label: str, host: str, port: int, tls: bool, username: str = "") -> int:
        cur = self.execute(
            "INSERT INTO saved_servers(label, host, port, tls, username, added_at)"
            " VALUES (?,?,?,?,?,?)",
            (label, host, int(port), 1 if tls else 0, username, time.time()),
        )
        return int(cur.lastrowid)

    def list_servers(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM saved_servers ORDER BY label")

    def remove_server(self, server_id: int) -> None:
        self.execute("DELETE FROM saved_servers WHERE id = ?", (server_id,))

    def mark_server(self, server_id: int, status: str) -> None:
        self.execute(
            "UPDATE saved_servers SET last_status = ?, last_seen = ? WHERE id = ?",
            (status, time.time(), server_id),
        )

    # transfers -------------------------------------------------------------
    def record_transfer(self, direction: str, peer: str, filename: str, size: int,
                        sha256: str, status: str, path: str) -> int:
        cur = self.execute(
            "INSERT INTO transfers(direction, peer, filename, size, sha256, status, path,"
            " created_at, completed_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (direction, peer, filename, size, sha256, status, path, time.time(),
             time.time() if status in ("done", "failed", "cancelled") else None),
        )
        return int(cur.lastrowid)

    def update_transfer(self, transfer_id: int, **fields: Any) -> None:
        allowed = {"status", "size", "sha256", "path", "completed_at"}
        sets = [f"{k} = ?" for k in fields if k in allowed]
        vals = [fields[k] for k in fields if k in allowed]
        if not sets:
            return
        if fields.get("status") in ("done", "failed", "cancelled") and "completed_at" not in fields:
            sets.append("completed_at = ?")
            vals.append(time.time())
        vals.append(transfer_id)
        self.execute(f"UPDATE transfers SET {', '.join(sets)} WHERE id = ?", tuple(vals))

    def list_transfers(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.query(
            "SELECT * FROM transfers ORDER BY created_at DESC LIMIT ?", (int(limit),)
        )

    # shared dirs -------------------------------------------------------------
    def add_shared_dir(self, path: str, label: str = "") -> int:
        cur = self.execute(
            "INSERT OR IGNORE INTO shared_dirs(path, label, added_at) VALUES (?,?,?)",
            (path, label, time.time()),
        )
        return int(cur.lastrowid or 0)

    def list_shared_dirs(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM shared_dirs ORDER BY label, path")

    def remove_shared_dir(self, dir_id: int) -> None:
        self.execute("DELETE FROM shared_dirs WHERE id = ?", (dir_id,))

    # invites / rooms -----------------------------------------------------------
    def create_invite(self, token: str, role: str, room_id: int | None,
                      max_uses: int, expires_at: float | None, created_by: int | None) -> None:
        self.execute(
            "INSERT INTO invites(token, role, room_id, max_uses, expires_at, created_by, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (token, role, room_id, max_uses, expires_at, created_by, time.time()),
        )

    def get_invite(self, token: str) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM invites WHERE token = ?", (token,))

    def consume_invite(self, token: str) -> None:
        self.execute("UPDATE invites SET uses = uses + 1 WHERE token = ?", (token,))

    def list_invites(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM invites ORDER BY created_at DESC")

    def delete_invite(self, token: str) -> None:
        self.execute("DELETE FROM invites WHERE token = ?", (token,))

    def create_room(self, name: str, code: str, created_by: int | None) -> int:
        cur = self.execute(
            "INSERT INTO rooms(name, code, created_at, created_by) VALUES (?,?,?,?)",
            (name, code, time.time(), created_by),
        )
        return int(cur.lastrowid)

    def list_rooms(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM rooms ORDER BY created_at DESC")

    def get_room_by_code(self, code: str) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM rooms WHERE code = ?", (code.upper(),))

    def delete_room(self, room_id: int) -> None:
        self.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
