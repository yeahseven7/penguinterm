"""Role hierarchy and permission checks shared by HTTP and WebSocket layers."""
from __future__ import annotations

from typing import Any

ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}


def role_at_least(user_role: str, required: str) -> bool:
    return ROLE_RANK.get(user_role, 0) >= ROLE_RANK.get(required, 99)


def can_manage_server(user: dict[str, Any]) -> bool:
    return user.get("role") == "owner"


def can_transfer_files(user: dict[str, Any]) -> bool:
    return user.get("role") in ("owner", "editor") and user.get("status") == "active"


def can_view_sessions(user: dict[str, Any]) -> bool:
    return user.get("status") == "active"
