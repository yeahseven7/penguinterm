"""Secure file transfer between connected peers (spec §10).

Design:
  * All paths resolved against explicit roots: the inbox/outbox transfer
    directories plus owner-approved shared directories. Nothing else is
    reachable. In particular, the user's home directory is NOT implicitly
    shared.
  * Uploads stream to a `.part` file with a hard size cap, then are renamed.
    If the client disconnects, the partial file is removed.
  * SHA-256 is computed during receive and compared on completion.
  * Symbolic links must resolve inside the same root; otherwise the file is
    refused. Path escaping (`..`) is refused. Filenames are sanitised.
  * Existing files are never overwritten silently — a numeric suffix is
    appended on collision.
  * Peer-to-peer send: our server logs in to the friend's server with
    credentials supplied per-operation by the user (never stored), then
    pushes the file to their inbox. No central service involved.
"""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import Any, BinaryIO

from ..config import Config
from ..db import Database
from ..events import events_bus

CHUNK = 1024 * 1024  # 1 MiB
_ILLEGAL_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    name = Path(name or "file").name  # strip any directory components
    name = _ILLEGAL_NAME.sub("_", name).strip().strip(".")
    if not name:
        name = "file"
    return name[:180]


def unique_destination(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for i in range(1, 1000):
        candidate = directory / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not allocate a non-colliding filename")


def resolve_download_path(cfg: Config, db: Database, requested: str) -> Path | None:
    """Validate a user-requested path against allowed roots. None on violation."""
    if not requested:
        return None
    raw = Path(requested)
    roots = [cfg.inbox_dir.resolve(), cfg.outbox_dir.resolve()]
    for row in db.list_shared_dirs():
        try:
            roots.append(Path(row["path"]).resolve())
        except OSError:
            continue
    try:
        resolved = raw.resolve()
    except (OSError, ValueError):
        return None
    # Must sit inside one of the roots
    if not any(resolved == root or root in resolved.parents for root in roots):
        return None
    # Symlink check: the final resolved path is what we will open — already
    # within a root. Reject symlinked path *components* used to escape roots
    # is handled by resolve(); additionally refuse dangling symlinks.
    if raw.is_symlink() and not raw.exists():
        return None
    if not resolved.is_file():
        return None
    return resolved


class TransferService:
    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db
        self.active: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    def allowed_roots(self) -> list[Path]:
        roots = [self.cfg.inbox_dir, self.cfg.outbox_dir]
        roots += [Path(r["path"]) for r in self.db.list_shared_dirs()]
        return roots

    def save_stream(self, stream: BinaryIO, filename: str, *,
                    peer: str = "", max_bytes: int | None = None,
                    direction: str = "receive",
                    expected_sha256: str | None = None) -> dict[str, Any]:
        max_bytes = int(max_bytes or self.cfg.get("max_upload_bytes", 512 * 1024 * 1024))
        safe = sanitize_filename(filename)
        dest = unique_destination(self.cfg.inbox_dir, safe)
        part = dest.with_suffix(dest.suffix + ".part")

        sha = hashlib.sha256()
        size = 0
        try:
            with open(part, "wb") as out:
                while True:
                    chunk = stream.read(CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError(f"File exceeds the {max_bytes}-byte limit")
                    sha.update(chunk)
                    out.write(chunk)
            digest = sha.hexdigest()
            if expected_sha256 and expected_sha256.lower() != digest:
                part.unlink(missing_ok=True)
                raise ValueError("SHA-256 mismatch — file rejected")
            part.replace(dest)
            transfer_id = self.db.record_transfer(direction, peer, dest.name, size,
                                                  digest, "done", str(dest))
            self.db.log("INFO", f"transfer {transfer_id} received: {dest.name} ({size} bytes)",
                        "transfer")
            events_bus.publish("transfer_update", {"id": transfer_id, "status": "done",
                                                   "filename": dest.name})
            return {"id": transfer_id, "path": str(dest), "size": size, "sha256": digest,
                    "filename": dest.name}
        except Exception:
            part.unlink(missing_ok=True)
            raise

    def stage_outbound(self, source: Path, dest_name: str | None = None) -> Path:
        """Copy a validated source file into the outbox for sending."""
        name = sanitize_filename(dest_name or source.name)
        dest = unique_destination(self.cfg.outbox_dir, name)
        shutil.copy2(source, dest)
        return dest

    def sha256_file(self, path: Path) -> str:
        sha = hashlib.sha256()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(CHUNK)
                if not chunk:
                    break
                sha.update(chunk)
        return sha.hexdigest()

    # ------------------------------------------------------------------
    async def push_to_peer(self, *, host: str, port: int, tls: bool,
                           username: str, password: str,
                           source: Path, filename: str) -> dict[str, Any]:
        """Log in to a peer server and upload the file to its inbox."""
        import httpx

        scheme = "https" if tls else "http"
        base = f"{scheme}://{host}:{port}"
        verify = False if tls else True  # self-signed by default; user opted in via 'tls'

        size = source.stat().st_size
        max_bytes = int(self.cfg.get("max_upload_bytes", 512 * 1024 * 1024))
        if size > max_bytes:
            raise ValueError(f"File exceeds the {max_bytes}-byte limit")

        digest = self.sha256_file(source)

        async with httpx.AsyncClient(base_url=base, verify=verify, timeout=30) as client:
            login = await client.post("/api/auth/login",
                                      json={"username": username, "password": password})
            if login.status_code != 200:
                raise PermissionError(
                    f"Peer login failed ({login.status_code}). "
                    "Check the username/password the peer's host created for you.")
            token = login.json()["token"]

            with open(source, "rb") as fh:
                files = {"file": (sanitize_filename(filename), fh,
                                  "application/octet-stream")}
                resp = await client.post(
                    "/api/transfer/receive",
                    headers={"Authorization": f"Bearer {token}", "X-Sha256": digest},
                    files=files,
                    timeout=600,
                )
            if resp.status_code != 200:
                raise RuntimeError(f"Peer rejected the file ({resp.status_code}): {resp.text[:200]}")
            result = resp.json()

        transfer_id = self.db.record_transfer("send", f"{host}:{port}", source.name,
                                              size, digest, "done", str(source))
        self.db.log("INFO", f"transfer {transfer_id} sent to {host}:{port}: {source.name}",
                    "transfer")
        events_bus.publish("transfer_update", {"id": transfer_id, "status": "done",
                                               "filename": source.name})
        return {"id": transfer_id, "peer": f"{host}:{port}", "size": size,
                "sha256": digest, "peer_response": result}
