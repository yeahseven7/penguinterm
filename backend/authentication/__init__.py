"""Account management and authentication.

First run creates the OWNER account (the person hosting this server).
Additional accounts are created by the owner with roles:

  OWNER  — full server management, invites, security, permissions
  EDITOR — join authorized shared terminals, execute when permitted, file transfer
  VIEWER — view terminal output only

New remote accounts start as 'pending' until the owner activates them when
`require_approval` is set; by default the owner explicitly creates accounts
and invites, so approval is inherent.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import Header, HTTPException, Request

from .. import security
from ..db import Database


class AuthService:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------
    def owner_exists(self) -> bool:
        return self.db.query_one("SELECT id FROM users WHERE role='owner' LIMIT 1") is not None

    def setup_owner(self, username: str, password: str) -> dict[str, Any]:
        if self.owner_exists():
            raise RuntimeError("Owner account already exists")
        if len(username) < 2 or len(password) < 8:
            raise ValueError("Username must be ≥2 chars and password ≥8 chars")
        password_hash, salt = security.hash_password(password)
        uid = self.db.create_user(username, password_hash, salt, role="owner", status="active")
        self.db.log("INFO", f"owner account '{username}' created", "auth")
        return self.db.get_user(uid) or {}

    def login(self, username: str, password: str) -> tuple[str, dict[str, Any]] | None:
        user = self.db.get_user_by_name(username)
        if not user:
            # Run a dummy verification to reduce username enumeration via timing.
            security.verify_password(password, "0" * 64, "00" * 16)
            return None
        if user["status"] == "blocked":
            return None
        if not security.verify_password(password, user["password_hash"], user["salt"]):
            return None
        token = security.new_token()
        self.db.store_token(security.hash_token(token), user["id"],
                            security.TOKEN_TTL_SECONDS, label="web")
        self.db.update_user(user["id"], last_seen=time.time())
        return token, user

    def authenticate(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        return self.db.token_user(security.hash_token(token))

    def logout(self, token: str) -> None:
        self.db.revoke_token(security.hash_token(token))

    def create_user(self, username: str, password: str, role: str) -> dict[str, Any]:
        if role not in security.ROLES:
            raise ValueError("Invalid role")
        if role == "owner":
            raise ValueError("The owner role cannot be assigned to additional accounts")
        if len(password) < 8:
            raise ValueError("Password must be ≥8 chars")
        password_hash, salt = security.hash_password(password)
        uid = self.db.create_user(username, password_hash, salt, role=role, status="active")
        self.db.log("INFO", f"user '{username}' created with role {role}", "auth")
        return self.db.get_user(uid) or {}

    def create_invite_account(self, username: str, role: str) -> tuple[str, dict[str, Any]]:
        """Invite flow: create account with a random password we hand back once."""
        password = security.new_token()[:20]
        user = self.create_user(username, password, role)
        return password, user

    def change_password(self, user_id: int, new_password: str) -> None:
        if len(new_password) < 8:
            raise ValueError("Password must be ≥8 chars")
        password_hash, salt = security.hash_password(new_password)
        self.db.update_user(user_id, password_hash=password_hash, salt=salt)
        self.db.log("INFO", f"password changed for user id {user_id}", "auth")


def require_role(*allowed: str):
    """FastAPI dependency factory enforcing account roles."""
    async def _dep(request: Request,
                   authorization: str | None = Header(default=None)) -> dict[str, Any]:
        auth: AuthService = request.app.state.auth
        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        else:
            token = request.cookies.get("pt_token", "")
        user = auth.authenticate(token) if token else None
        if not user:
            raise HTTPException(status_code=401, detail="Authentication required")
        if user["status"] != "active":
            raise HTTPException(status_code=403, detail="Account is not active")
        if allowed and user["role"] not in allowed:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return _dep
