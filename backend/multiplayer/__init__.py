"""Rooms, invitation links and saved servers (friends).

PenguinTerm is decentralized: a "friend" is another PenguinTerm server you
know about. You connect to it directly over TCP/HTTPS/WSS with an account
the host created for you — there is no mandatory central service.
"""
from __future__ import annotations

import time
import secrets
from typing import Any

from ..db import Database
from .. import security


class RoomService:
    def __init__(self, db: Database):
        self.db = db

    # rooms ------------------------------------------------------------
    def create_room(self, name: str, created_by: int | None) -> dict[str, Any]:
        code = security.new_room_code()
        for _ in range(10):
            if not self.db.get_room_by_code(code):
                break
            code = security.new_room_code()
        room_id = self.db.create_room(name.strip()[:80] or "Room", code, created_by)
        self.db.log("INFO", f"room '{name}' created with code {code}", "rooms")
        return self.db.query_one("SELECT * FROM rooms WHERE id = ?", (room_id,)) or {}

    def join_room(self, code: str, user_id: int, role: str = "editor") -> dict[str, Any]:
        room = self.db.get_room_by_code(code.upper().strip())
        if not room:
            raise ValueError("Invalid room code")
        self.db.execute(
            "INSERT OR IGNORE INTO room_members(room_id, user_id, role) VALUES (?,?,?)",
            (room["id"], user_id, role),
        )
        return room

    # invites ------------------------------------------------------------
    def create_invite(self, *, role: str, room_id: int | None,
                      max_uses: int, expires_hours: float | None,
                      created_by: int | None) -> dict[str, Any]:
        if role not in security.ROLES:
            raise ValueError("Invalid role")
        token = security.new_token()
        expires_at = time.time() + expires_hours * 3600 if expires_hours else None
        self.db.create_invite(token, role, room_id, max(1, int(max_uses)),
                              expires_at, created_by)
        self.db.log("INFO", f"invite created (role={role}, max_uses={max_uses})", "auth")
        return {"token": token, "role": role, "room_id": room_id,
                "max_uses": max_uses, "expires_at": expires_at}

    def validate_invite(self, token: str) -> dict[str, Any] | None:
        invite = self.db.get_invite(token)
        if not invite:
            return None
        if invite["expires_at"] and invite["expires_at"] < time.time():
            return None
        if invite["uses"] >= invite["max_uses"]:
            return None
        return invite

    def consume_invite(self, token: str) -> None:
        self.db.consume_invite(token)

    # saved servers --------------------------------------------------------
    def add_saved_server(self, label: str, host: str, port: int,
                         tls: bool, username: str = "") -> int:
        return self.db.add_server(label.strip()[:80], host.strip()[:200],
                                  int(port), tls, username.strip()[:64])

    @staticmethod
    def invite_url(base_url: str, token: str) -> str:
        return f"{base_url.rstrip('/')}/?invite={token}"
