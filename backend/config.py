"""PenguinTerm server configuration.

Configuration is stored as JSON in the platform-specific data directory.
Nothing in the source tree needs to be edited by the user; `penguinterm
configure` / the installers generate this file, and the Settings page can
update it at runtime (a restart is required for bind/TLS changes).
"""
from __future__ import annotations

import json
import os
import sys
import secrets
from pathlib import Path
from typing import Any

APP_NAME = "PenguinTerm"
APP_VERSION = "1.0.0"
DEFAULT_PORT = 8443


def data_dir() -> Path:
    """Platform-appropriate per-user data directory."""
    env = os.environ.get("PENGUINTERM_DATA_DIR")
    if env:
        d = Path(env)
    elif sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        d = Path(base) / APP_NAME
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        xdg = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
        d = Path(xdg) / "penguinterm"
    d.mkdir(parents=True, exist_ok=True)
    return d


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


DEFAULTS: dict[str, Any] = {
    # network
    "host": "0.0.0.0",
    "port": DEFAULT_PORT,
    "tls": True,
    # terminal
    "scrollback_lines": 5000,
    "max_sessions": 16,
    "max_output_buffer": 2_000_000,  # bytes kept per session for replay
    # security
    "restricted_mode": True,          # on by default for remote users
    "allow_owner_full_shell": True,   # owner's local terminals are unrestricted
    "max_upload_bytes": 512 * 1024 * 1024,
    "allowed_roots": [],              # extra shared directories (absolute paths)
    "remote_cwd_jail": "home",        # 'home' | 'path' | 'none'  (remote sessions only)
    "remote_cwd_jail_path": "",
    # compatibility layer
    "compat_layer": False,            # optional Linux<->Windows command aliases
    # networking helpers
    "public_ip_service": "https://api.ipify.org",
    "relay_url": "",                  # optional self-hosted relay, e.g. wss://relay.example.com/ws
    "zerotier_enabled": False,
    "tailscale_enabled": False,
    # housekeeping
    "session_idle_minutes": 0,        # 0 = never reap idle sessions
    "log_level": "INFO",
}


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "config.json")
        self._values: dict[str, Any] = dict(DEFAULTS)
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    self._values.update(json.load(fh))
            except Exception:
                # A corrupt config must not prevent startup; back it up and use defaults.
                try:
                    self.path.replace(self.path.with_suffix(".corrupt.json"))
                except OSError:
                    pass
        # Ensure a stable install token exists (used for relay rendezvous).
        if not (data_dir() / "install.token").exists():
            (data_dir() / "install.token").write_text(secrets.token_hex(16), encoding="utf-8")

    # ------------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any, persist: bool = True) -> None:
        self._values[key] = value
        if persist:
            self.save()

    def as_dict(self) -> dict[str, Any]:
        return dict(self._values)

    def update(self, patch: dict[str, Any], persist: bool = True) -> None:
        for key, value in patch.items():
            if key in DEFAULTS:
                self._values[key] = value
        if persist:
            self.save()

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._values, fh, indent=2, sort_keys=True)
        tmp.replace(self.path)

    # ------------------------------------------------------------------
    @property
    def inbox_dir(self) -> Path:
        d = data_dir() / "transfers" / "inbox"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def outbox_dir(self) -> Path:
        d = data_dir() / "transfers" / "outbox"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def shim_dir(self) -> Path:
        """PATH-interposition directory for restricted mode (external commands only)."""
        d = data_dir() / "shim"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def tls_dir(self) -> Path:
        d = data_dir() / "tls"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def db_path(self) -> Path:
        return data_dir() / "penguinterm.db"


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config
