"""PenguinTerm FastAPI application and WebSocket gateway.

Run:
    python -m backend.main                # uses configured host/port/TLS
    python -m backend.main --port 8443
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import platform
import socket
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import (
    Depends, FastAPI, File, Header, HTTPException, Request, UploadFile,
    WebSocket, WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .authentication import AuthService, require_role
from .compat import translate_line, ensure_compat_shims
from .config import get_config, project_root, Config
from .db import Database
from .events import events_bus
from .file_transfer import TransferService, resolve_download_path, sanitize_filename
from .multiplayer import RoomService
from .networking.diagnostics import collect_diagnostics
from .permissions import can_transfer_files
from .security import ensure_shims
from .shells import detect_shells, default_shell
from .terminal import SessionManager

cfg: Config = get_config()
db: Database  # set in lifespan
auth: AuthService
rooms: RoomService
transfers: TransferService
sessions: SessionManager

START_TIME = time.time()
ACTIVE_WS: set[WebSocket] = set()
_login_attempts: dict[str, list[float]] = {}
LOGIN_WINDOW_SECONDS = 60
LOGIN_MAX_ATTEMPTS = 10


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global db, auth, rooms, transfers, sessions
    db = Database(cfg.db_path)
    auth = AuthService(db)
    rooms = RoomService(db)
    transfers = TransferService(cfg, db)
    sessions = SessionManager(cfg, db)
    events_bus.bind_loop(asyncio.get_running_loop())
    ensure_shims(cfg.shim_dir)
    ensure_compat_shims(cfg.shim_dir.parent / "compat")
    app.state.db = db
    app.state.auth = auth
    app.state.sessions = sessions
    db.purge_expired_tokens()
    db.log("INFO", f"PenguinTerm {__version__} starting (platform={sys.platform})", "server")
    yield
    db.log("INFO", "PenguinTerm shutting down", "server")
    sessions.shutdown_all()
    db.close()


app = FastAPI(title="PenguinTerm", version=__version__, lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)

# Same-origin app; no cross-origin API access. (Relay/Tailscale users hit this
# over their private link, still same-origin.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[], allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "PUT"],
    allow_headers=["Authorization", "Content-Type", "X-Sha256"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self' ws: wss:"
    )
    return response


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------
class SetupBody(BaseModel):
    username: str
    password: str


class LoginBody(BaseModel):
    username: str
    password: str


class InviteRegisterBody(BaseModel):
    invite_token: str
    username: str
    password: str


class ChangePasswordBody(BaseModel):
    new_password: str


class CreateUserBody(BaseModel):
    username: str
    password: str
    role: str = "viewer"


class PatchUserBody(BaseModel):
    role: str | None = None
    status: str | None = None


class CreateInviteBody(BaseModel):
    role: str = "viewer"
    max_uses: int = 1
    expires_hours: float | None = 72
    room_id: int | None = None


class CreateRoomBody(BaseModel):
    name: str


class JoinRoomBody(BaseModel):
    code: str


class CreateSessionBody(BaseModel):
    shell_id: str | None = None
    title: str | None = None
    kind: str = "local"            # 'local' | 'shared'
    input_mode: str = "all"        # 'all' | 'owner'
    cols: int = 120
    rows: int = 30


class PatchSessionBody(BaseModel):
    title: str | None = None
    input_mode: str | None = None


class SaveServerBody(BaseModel):
    label: str
    host: str
    port: int = 8443
    tls: bool = True
    username: str = ""


class ProbeServerBody(BaseModel):
    host: str
    port: int = 8443
    tls: bool = True


class SharedDirBody(BaseModel):
    path: str
    label: str = ""


class SendToPeerBody(BaseModel):
    server_id: int | None = None
    host: str | None = None
    port: int = 8443
    tls: bool = True
    username: str
    password: str
    path: str


class SettingsPatch(BaseModel):
    values: dict[str, Any]


class ShutdownBody(BaseModel):
    confirm: bool = False


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def token_from_request(request: Request,
                       authorization: str | None = Header(default=None)) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.cookies.get("pt_token", "")


def ws_token(ws: WebSocket) -> str:
    token = ws.query_params.get("token", "")
    if token:
        return token
    return ws.cookies.get("pt_token", "")


async def authenticate_ws(ws: WebSocket) -> dict[str, Any] | None:
    return auth.authenticate(ws_token(ws))


def client_ip(request_or_ws) -> str:
    client = getattr(request_or_ws, "client", None)
    return client.host if client else "unknown"


def rate_limit_login(key: str) -> bool:
    now = time.time()
    attempts = [t for t in _login_attempts.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
    _login_attempts[key] = attempts
    if len(attempts) >= LOGIN_MAX_ATTEMPTS:
        return False
    attempts.append(now)
    return True


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {"id": user["id"], "username": user["username"], "role": user["role"],
            "status": user["status"], "last_seen": user.get("last_seen")}


# ---------------------------------------------------------------------------
# health & auth
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    return {"ok": True, "service": "PenguinTerm", "version": __version__,
            "platform": sys.platform, "uptime_seconds": int(time.time() - START_TIME)}


@app.get("/api/auth/status")
async def auth_status():
    return {"setup_required": not auth.owner_exists(), "version": __version__}


@app.post("/api/auth/setup")
async def auth_setup(body: SetupBody):
    if auth.owner_exists():
        raise HTTPException(400, "Owner account already exists")
    try:
        user = auth.setup_owner(body.username.strip(), body.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    token, _ = auth.login(body.username.strip(), body.password) or (None, None)
    return {"token": token, "user": public_user(user)}


@app.post("/api/auth/login")
async def auth_login(body: LoginBody, request: Request,
                     authorization: str | None = Header(default=None)):
    key = f"{client_ip(request)}:{body.username.lower()}"
    if not rate_limit_login(key):
        raise HTTPException(429, "Too many login attempts — wait a minute")
    result = auth.login(body.username.strip(), body.password)
    if not result:
        db.log("WARN", f"failed login for '{body.username}' from {client_ip(request)}", "auth")
        raise HTTPException(401, "Invalid username or password")
    token, user = result
    response = JSONResponse({"token": token, "user": public_user(user)})
    response.set_cookie("pt_token", token, httponly=True, samesite="lax",
                        secure=bool(cfg.get("tls")), max_age=60 * 60 * 24 * 14)
    return response


@app.post("/api/auth/logout")
async def auth_logout(request: Request,
                      authorization: str | None = Header(default=None)):
    token = token_from_request(request, authorization)
    if token:
        auth.logout(token)
    response = JSONResponse({"ok": True})
    response.delete_cookie("pt_token")
    return response


@app.get("/api/auth/me")
async def auth_me(user: dict = Depends(require_role())):
    return public_user(user)


@app.post("/api/auth/change-password")
async def auth_change_password(body: ChangePasswordBody,
                               user: dict = Depends(require_role())):
    try:
        auth.change_password(user["id"], body.new_password)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"ok": True}


@app.post("/api/auth/register-with-invite")
async def register_with_invite(body: InviteRegisterBody):
    invite = rooms.validate_invite(body.invite_token)
    if not invite:
        raise HTTPException(403, "Invitation is invalid, expired or exhausted")
    try:
        user = auth.create_user(body.username.strip(), body.password, invite["role"])
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(409, f"Could not create account: {exc}")
    rooms.consume_invite(body.invite_token)
    if invite.get("room_id"):
        db.execute("INSERT OR IGNORE INTO room_members(room_id, user_id, role) VALUES (?,?,?)",
                   (invite["room_id"], user["id"], invite["role"]))
    token, _ = auth.login(body.username.strip(), body.password) or (None, None)
    response = JSONResponse({"token": token, "user": public_user(user)})
    response.set_cookie("pt_token", token, httponly=True, samesite="lax",
                        secure=bool(cfg.get("tls")), max_age=60 * 60 * 24 * 14)
    return response


# ---------------------------------------------------------------------------
# users (owner)
# ---------------------------------------------------------------------------
@app.get("/api/users")
async def users_list(user: dict = Depends(require_role("owner"))):
    return {"users": db.list_users()}


@app.post("/api/users")
async def users_create(body: CreateUserBody, user: dict = Depends(require_role("owner"))):
    try:
        created = auth.create_user(body.username.strip(), body.password, body.role)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(409, f"Could not create user: {exc}")
    return public_user(created)


@app.patch("/api/users/{user_id}")
async def users_patch(user_id: int, body: PatchUserBody,
                      user: dict = Depends(require_role("owner"))):
    target = db.get_user(user_id)
    if not target:
        raise HTTPException(404, "User not found")
    if target["role"] == "owner" and body.role and body.role != "owner":
        raise HTTPException(400, "The owner role cannot be demoted")
    patch: dict[str, Any] = {}
    if body.role and body.role in ("editor", "viewer", "owner"):
        patch["role"] = body.role
    if body.status and body.status in ("active", "pending", "blocked"):
        patch["status"] = body.status
    db.update_user(user_id, **patch)
    return public_user(db.get_user(user_id) or {})


@app.delete("/api/users/{user_id}")
async def users_delete(user_id: int, user: dict = Depends(require_role("owner"))):
    target = db.get_user(user_id)
    if target and target["role"] == "owner":
        raise HTTPException(400, "Cannot delete the owner account")
    db.delete_user(user_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# invites & rooms
# ---------------------------------------------------------------------------
@app.get("/api/invites")
async def invites_list(user: dict = Depends(require_role("owner"))):
    return {"invites": db.list_invites()}


@app.post("/api/invites")
async def invites_create(body: CreateInviteBody, request: Request,
                         user: dict = Depends(require_role("owner"))):
    try:
        invite = rooms.create_invite(role=body.role, room_id=body.room_id,
                                     max_uses=body.max_uses,
                                     expires_hours=body.expires_hours,
                                     created_by=user["id"])
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    base = str(request.base_url).rstrip("/")
    invite["url"] = rooms.invite_url(base, invite["token"])
    return invite


@app.delete("/api/invites/{token}")
async def invites_delete(token: str, user: dict = Depends(require_role("owner"))):
    db.delete_invite(token)
    return {"ok": True}


@app.get("/api/rooms")
async def rooms_list(user: dict = Depends(require_role())):
    return {"rooms": db.list_rooms()}


@app.post("/api/rooms")
async def rooms_create(body: CreateRoomBody, user: dict = Depends(require_role("owner"))):
    return rooms.create_room(body.name, created_by=user["id"])


@app.post("/api/rooms/join")
async def rooms_join(body: JoinRoomBody, user: dict = Depends(require_role())):
    try:
        room = rooms.join_room(body.code, user["id"])
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return room


@app.delete("/api/rooms/{room_id}")
async def rooms_delete(room_id: int, user: dict = Depends(require_role("owner"))):
    db.delete_room(room_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# system & shells
# ---------------------------------------------------------------------------
@app.get("/api/system")
async def system_info(user: dict = Depends(require_role())):
    shells = detect_shells()
    return {
        "platform": sys.platform,
        "os_name": platform.system() or sys.platform,
        "os_release": platform.release(),
        "machine": platform.machine(),
        "hostname": socket.gethostname(),
        "python": platform.python_version(),
        "shells": shells,
        "default_shell": default_shell(shells),
        "version": __version__,
    }


@app.get("/api/shells")
async def shells_list(user: dict = Depends(require_role())):
    return {"shells": detect_shells()}


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------
@app.get("/api/sessions")
async def sessions_list(user: dict = Depends(require_role())):
    return {"sessions": sessions.list_for(user)}


@app.post("/api/sessions")
async def sessions_create(body: CreateSessionBody, user: dict = Depends(require_role())):
    try:
        session = sessions.create(
            shell_id=body.shell_id, title=body.title, kind=body.kind,
            user=user, input_mode=body.input_mode, cols=body.cols, rows=body.rows)
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return sessions.describe(session, user)


@app.patch("/api/sessions/{session_id}")
async def sessions_patch(session_id: str, body: PatchSessionBody,
                         user: dict = Depends(require_role())):
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if body.title is not None:
        if session.created_by != user["id"] and user["role"] != "owner":
            raise HTTPException(403, "Not your session")
        session.title = body.title[:120]
    if body.input_mode is not None:
        if user["role"] != "owner":
            raise HTTPException(403, "Only the owner can change input mode")
        session.input_mode = body.input_mode if body.input_mode in ("all", "owner") else "owner"
    return sessions.describe(session, user)


@app.post("/api/sessions/{session_id}/duplicate")
async def sessions_duplicate(session_id: str, user: dict = Depends(require_role())):
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    try:
        new_session = sessions.create(
            shell_id=session.shell_id, title=f"{session.title} (copy)",
            kind=session.kind, user=user, input_mode=session.input_mode,
            cols=session.cols, rows=session.rows)
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return sessions.describe(new_session, user)


@app.delete("/api/sessions/{session_id}")
async def sessions_delete(session_id: str, user: dict = Depends(require_role())):
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if not sessions.close(session, user):
        raise HTTPException(403, "Not allowed to close this session")
    return {"ok": True}


# ---------------------------------------------------------------------------
# saved servers (friends) & probing
# ---------------------------------------------------------------------------
@app.get("/api/servers")
async def servers_list(user: dict = Depends(require_role())):
    return {"servers": db.list_servers()}


@app.post("/api/servers")
async def servers_add(body: SaveServerBody, user: dict = Depends(require_role())):
    server_id = rooms.add_saved_server(body.label, body.host, body.port,
                                       body.tls, body.username)
    db.log("INFO", f"saved server '{body.label}' ({body.host}:{body.port}) added", "net")
    return db.query_one("SELECT * FROM saved_servers WHERE id = ?", (server_id,))


@app.delete("/api/servers/{server_id}")
async def servers_delete(server_id: int, user: dict = Depends(require_role())):
    db.remove_server(server_id)
    return {"ok": True}


@app.post("/api/servers/{server_id}/test")
async def servers_test(server_id: int, user: dict = Depends(require_role())):
    row = db.query_one("SELECT * FROM saved_servers WHERE id = ?", (server_id,))
    if not row:
        raise HTTPException(404, "Server not found")
    result = await _probe(row["host"], row["port"], bool(row["tls"]))
    db.mark_server(server_id, "online" if result["reachable"] else "offline")
    return result


@app.post("/api/servers/probe")
async def servers_probe(body: ProbeServerBody, user: dict = Depends(require_role())):
    return await _probe(body.host, body.port, body.tls)


async def _probe(host: str, port: int, tls: bool) -> dict[str, Any]:
    """Reachability probe. Only reports what it actually measured."""
    import httpx
    import time as _time

    scheme = "https" if tls else "http"
    url = f"{scheme}://{host}:{int(port)}/api/health"
    started = _time.time()
    try:
        async with httpx.AsyncClient(verify=False if tls else True, timeout=5) as client:
            resp = await client.get(url)
        elapsed = int((_time.time() - started) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and data.get("service") == "PenguinTerm":
                return {"reachable": True, "latency_ms": elapsed,
                        "service": "PenguinTerm",
                        "version": data.get("version"),
                        "platform": data.get("platform")}
            return {"reachable": False, "latency_ms": elapsed,
                    "detail": "Port is open but it is not a PenguinTerm server"}
        return {"reachable": False, "latency_ms": elapsed,
                "detail": f"HTTP {resp.status_code}"}
    except Exception as exc:
        return {"reachable": False, "latency_ms": None,
                "detail": f"Connection failed: {exc.__class__.__name__}"}


# ---------------------------------------------------------------------------
# diagnostics & logs
# ---------------------------------------------------------------------------
@app.get("/api/diagnostics")
async def diagnostics(user: dict = Depends(require_role("owner"))):
    return collect_diagnostics(cfg, active_connections=len(ACTIVE_WS))


@app.get("/api/logs")
async def logs_list(limit: int = 200, user: dict = Depends(require_role("owner"))):
    return {"logs": db.recent_logs(limit)}


# ---------------------------------------------------------------------------
# file transfers
# ---------------------------------------------------------------------------
@app.get("/api/transfers")
async def transfers_list(user: dict = Depends(require_role("owner", "editor"))):
    return {"transfers": db.list_transfers(100)}


@app.delete("/api/transfers/{transfer_id}")
async def transfers_delete(transfer_id: int, user: dict = Depends(require_role("owner", "editor"))):
    row = db.query_one("SELECT * FROM transfers WHERE id = ?", (transfer_id,))
    if not row:
        raise HTTPException(404, "Transfer not found")
    # Removing a record also removes the payload if it still lives in our inbox/outbox.
    path = Path(row["path"]) if row.get("path") else None
    if path:
        resolved = resolve_download_path(cfg, db, str(path))
        if resolved:
            try:
                resolved.unlink()
            except OSError:
                pass
    db.execute("DELETE FROM transfers WHERE id = ?", (transfer_id,))
    return {"ok": True}


@app.post("/api/transfer/upload")
async def transfer_upload(request: Request, file: UploadFile = File(...),
                          user: dict = Depends(require_role())):
    if not can_transfer_files(user):
        raise HTTPException(403, "File transfer requires the editor role or above")
    expected = request.headers.get("X-Sha256")
    try:
        result = transfers.save_stream(file.file, file.filename or "file",
                                       peer="local-upload",
                                       expected_sha256=expected)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return result


@app.post("/api/transfer/receive")
async def transfer_receive(request: Request, file: UploadFile = File(...),
                           user: dict = Depends(require_role())):
    """Peer-to-peer inbox. The peer authenticates with their own account here."""
    if not can_transfer_files(user):
        raise HTTPException(403, "File transfer requires the editor role or above")
    peer = f"{user['username']}@{client_ip(request)}"
    expected = request.headers.get("X-Sha256")
    try:
        result = transfers.save_stream(file.file, file.filename or "file",
                                       peer=peer, expected_sha256=expected)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    db.log("INFO", f"received {result['filename']} from peer {peer}", "transfer")
    return result


@app.get("/api/transfer/download")
async def transfer_download(path: str | None = None, transfer_id: int | None = None,
                            user: dict = Depends(require_role("owner", "editor"))):
    target: Path | None = None
    transfer_row = None
    if transfer_id is not None:
        transfer_row = db.query_one("SELECT * FROM transfers WHERE id = ?", (transfer_id,))
        if not transfer_row:
            raise HTTPException(404, "Transfer not found")
        target = resolve_download_path(cfg, db, transfer_row.get("path") or "")
    elif path:
        target = resolve_download_path(cfg, db, path)
    if target is None:
        raise HTTPException(403, "Path is outside the allowed transfer/shared roots")

    sha = transfers.sha256_file(target)
    filename = sanitize_filename(target.name)

    def iterfile():
        with open(target, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Sha256": sha,
        "Content-Length": str(target.stat().st_size),
    }
    return StreamingResponse(iterfile(), media_type="application/octet-stream",
                             headers=headers)


@app.post("/api/transfer/send-to-peer")
async def transfer_send_to_peer(body: SendToPeerBody,
                                user: dict = Depends(require_role())):
    if not can_transfer_files(user):
        raise HTTPException(403, "File transfer requires the editor role or above")
    target = resolve_download_path(cfg, db, body.path)
    if target is None:
        raise HTTPException(403, "Path is outside the allowed transfer/shared roots")

    host, port, tls = body.host, body.port, body.tls
    if body.server_id is not None:
        row = db.query_one("SELECT * FROM saved_servers WHERE id = ?", (body.server_id,))
        if not row:
            raise HTTPException(404, "Saved server not found")
        host, port, tls = row["host"], row["port"], bool(row["tls"])
    if not host:
        raise HTTPException(422, "A host or server_id is required")

    try:
        result = await transfers.push_to_peer(
            host=host, port=int(port), tls=bool(tls),
            username=body.username, password=body.password,
            source=target, filename=target.name)
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except Exception as exc:
        db.log("WARN", f"peer transfer failed: {exc}", "transfer")
        raise HTTPException(502, f"Peer transfer failed: {exc}")
    return result


@app.get("/api/shared-dirs")
async def shared_dirs_list(user: dict = Depends(require_role("owner"))):
    return {"directories": db.list_shared_dirs()}


@app.post("/api/shared-dirs")
async def shared_dirs_add(body: SharedDirBody, user: dict = Depends(require_role("owner"))):
    path = Path(body.path).expanduser()
    if not path.is_dir():
        raise HTTPException(422, "Not a directory")
    resolved = str(path.resolve())
    db.add_shared_dir(resolved, body.label)
    # Persist to config allowed_roots too so restarts keep behavior consistent.
    allowed = set(cfg.get("allowed_roots", []))
    allowed.add(resolved)
    cfg.set("allowed_roots", sorted(allowed))
    return {"directories": db.list_shared_dirs()}


@app.delete("/api/shared-dirs/{dir_id}")
async def shared_dirs_delete(dir_id: int, user: dict = Depends(require_role("owner"))):
    db.remove_shared_dir(dir_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# settings & server management
# ---------------------------------------------------------------------------
EDITABLE_SETTINGS = {
    "host", "port", "tls",
    "scrollback_lines", "max_sessions", "max_output_buffer",
    "restricted_mode", "allow_owner_full_shell", "remote_create_sessions",
    "remote_cwd_jail", "remote_cwd_jail_path",
    "max_upload_bytes", "compat_layer",
    "public_ip_service", "relay_url", "tailscale_enabled", "zerotier_enabled",
    "session_idle_minutes", "log_level",
}
RESTART_REQUIRED = {"host", "port", "tls"}


@app.get("/api/settings")
async def settings_get(user: dict = Depends(require_role("owner"))):
    values = cfg.as_dict()
    return {"settings": {k: values.get(k) for k in sorted(EDITABLE_SETTINGS)},
            "restart_required_keys": sorted(RESTART_REQUIRED)}


@app.put("/api/settings")
async def settings_put(body: SettingsPatch, user: dict = Depends(require_role("owner"))):
    patch = {k: v for k, v in body.values.items() if k in EDITABLE_SETTINGS}
    restart = bool(RESTART_REQUIRED & patch.keys())
    cfg.update(patch)
    db.log("INFO", f"settings updated: {', '.join(sorted(patch)) or '(no changes)'}", "settings")
    return {"ok": True, "restart_required": restart,
            "settings": {k: cfg.get(k) for k in sorted(EDITABLE_SETTINGS)}}


@app.get("/api/server/status")
async def server_status(user: dict = Depends(require_role("owner"))):
    resource = {"cpu_percent": None, "memory_mb": None, "threads": None}
    try:
        import psutil
        proc = psutil.Process()
        resource = {
            "cpu_percent": proc.cpu_percent(interval=0.1),
            "memory_mb": round(proc.memory_info().rss / 1024 / 1024, 1),
            "threads": proc.num_threads(),
        }
    except Exception:
        pass
    return {
        "status": "running",
        "version": __version__,
        "uptime_seconds": int(time.time() - START_TIME),
        "platform": sys.platform,
        "host": cfg.get("host"),
        "port": cfg.get("port"),
        "tls": bool(cfg.get("tls")),
        "active_ws_connections": len(ACTIVE_WS),
        "active_sessions": sum(1 for s in sessions.sessions.values() if not s.exited),
        "total_sessions": len(sessions.sessions),
        "resource": resource,
        "config_path": str(cfg.path),
        "data_dir": str(cfg.path.parent),
    }


@app.post("/api/server/shutdown")
async def server_shutdown(body: ShutdownBody, user: dict = Depends(require_role("owner"))):
    if not body.confirm:
        raise HTTPException(400, "confirm=true is required to stop the server")

    async def _stop() -> None:
        await asyncio.sleep(0.4)
        os._exit(0)

    asyncio.get_running_loop().create_task(_stop())
    db.log("INFO", f"server shutdown requested by {user['username']}", "server")
    return {"ok": True, "detail": "Server is shutting down"}


@app.post("/api/server/restart")
async def server_restart(body: ShutdownBody, user: dict = Depends(require_role("owner"))):
    """Exit nonzero so a service manager (systemd / Scheduled Task) restarts us."""
    if not body.confirm:
        raise HTTPException(400, "confirm=true is required to restart the server")

    async def _stop() -> None:
        await asyncio.sleep(0.4)
        os._exit(75)  # EX_TEMPFAIL — conventional "restart me" code

    asyncio.get_running_loop().create_task(_stop())
    db.log("INFO", f"server restart requested by {user['username']}", "server")
    return {"ok": True, "detail": "Server is restarting"}


# ---------------------------------------------------------------------------
# websockets
# ---------------------------------------------------------------------------
async def _ws_require_user(ws: WebSocket) -> dict[str, Any] | None:
    user = await authenticate_ws(ws)
    if not user or user.get("status") != "active":
        await ws.close(code=4401)
        return None
    return user


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    user = await _ws_require_user(ws)
    if not user:
        return
    await ws.accept()
    ACTIVE_WS.add(ws)
    queue = await events_bus.subscribe()
    try:
        await ws.send_text(json.dumps({"event": "hello", "payload":
                                       {"username": user["username"], "role": user["role"]},
                                       "ts": time.time()}))
        while True:
            message = await queue.get()
            await ws.send_text(json.dumps(message))
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
    finally:
        ACTIVE_WS.discard(ws)
        await events_bus.unsubscribe(queue)


@app.websocket("/ws/term/{session_id}")
async def ws_term(ws: WebSocket, session_id: str):
    user = await _ws_require_user(ws)
    if not user:
        return

    session = sessions.get(session_id)
    if session is None:
        await ws.close(code=4404)
        return
    try:
        sub, replay = sessions.attach(session, user)
    except PermissionError:
        await ws.close(code=4403)
        return

    await ws.accept()
    ACTIVE_WS.add(ws)
    db.log("INFO", f"ws attach: {user['username']} -> session {session_id}", "net")

    # session metadata, then replay, then live stream
    await ws.send_text(json.dumps({
        "type": "meta",
        **sessions.describe(session, user),
        "your_role": user["role"],
    }))
    if replay:
        await ws.send_bytes(replay)
    await ws.send_text(json.dumps({"type": "replay_done"}))
    sessions.broadcast_viewers(session)

    async def forward_output():
        try:
            while True:
                item = await sub.queue.get()
                if item is None:
                    await ws.send_text(json.dumps({"type": "exit",
                                                   "exit_code": session.exit_code}))
                    return
                await ws.send_bytes(item)
        except (WebSocketDisconnect, RuntimeError):
            return

    async def forward_ctrl():
        try:
            while True:
                message = await sub.ctrl_queue.get()
                await ws.send_text(json.dumps(message))
        except (WebSocketDisconnect, RuntimeError):
            return

    out_task = asyncio.create_task(forward_output())
    ctrl_task = asyncio.create_task(forward_ctrl())
    compat_enabled = bool(cfg.get("compat_layer", False))

    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data is not None:
                ok = await sessions.write(session, user, data)
                if not ok:
                    await ws.send_text(json.dumps({"type": "denied",
                                                   "reason": "Input is not permitted in this session"}))
                continue
            text = message.get("text")
            if not text:
                continue
            try:
                cmd = json.loads(text)
            except json.JSONDecodeError:
                continue
            mtype = cmd.get("type")
            if mtype == "resize":
                try:
                    sessions.resize(session, int(cmd.get("cols", 120)), int(cmd.get("rows", 30)))
                except (TypeError, ValueError):
                    pass
            elif mtype == "ping":
                await ws.send_text(json.dumps({"type": "pong", "ts": time.time()}))
            elif mtype == "complete_command" and compat_enabled:
                # Complete single-line command translation (compat layer).
                line = str(cmd.get("line", ""))
                translated, note = translate_line(line)
                if note:
                    await sessions.write(session, user, (translated + "\r").encode())
                    await ws.send_text(json.dumps({"type": "compat", "note": note}))
                else:
                    await sessions.write(session, user, (line + "\r").encode())
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        out_task.cancel()
        ctrl_task.cancel()
        ACTIVE_WS.discard(ws)
        sessions.detach(session, sub)
        sessions.broadcast_viewers(session)
        db.log("INFO", f"ws detach: {user['username']} <- session {session_id}", "net")


# ---------------------------------------------------------------------------
# static frontend
# ---------------------------------------------------------------------------
FRONTEND_DIR = project_root() / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="PenguinTerm server")
    parser.add_argument("--host", default=None, help="Override configured bind host")
    parser.add_argument("--port", type=int, default=None, help="Override configured port")
    parser.add_argument("--no-tls", action="store_true", help="Serve plain HTTP (dev only)")
    args = parser.parse_args()

    host = args.host or cfg.get("host", "0.0.0.0")
    port = args.port or int(cfg.get("port", 8443))
    tls_enabled = bool(cfg.get("tls", True)) and not args.no_tls

    import uvicorn

    ssl_kwargs: dict[str, Any] = {}
    if tls_enabled:
        from .tls import ensure_self_signed
        cert, key = ensure_self_signed(cfg.tls_dir,
                                       host if host not in ("0.0.0.0", "::") else "127.0.0.1")
        ssl_kwargs = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}

    print(f"PenguinTerm {__version__} — decentralized multiplayer terminal", flush=True)
    print(f"  platform : {sys.platform}", flush=True)
    print(f"  bind     : {host}:{port}  (tls={'yes' if tls_enabled else 'NO — dev mode'})",
          flush=True)
    print(f"  data dir : {cfg.path.parent}", flush=True)

    uvicorn.run(app, host=host, port=port, log_level=str(cfg.get("log_level", "info")).lower(),
                **ssl_kwargs)


if __name__ == "__main__":
    main()
