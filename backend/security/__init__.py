"""PenguinTerm security primitives.

Layered model — no single mechanism is treated as a boundary:

  1. Identity & authorization (accounts, roles, approval for new users).
  2. Optional sandbox for remote execution:
       - Linux: setrlimit resource limits + PATH shim interposition +
         environment scrubbing + cwd jail. Where available, bwrap is used
         for filesystem namespacing.
       - Windows: Job Object limits (process cap, memory cap, kill on close)
         + PATH shim interposition + environment scrubbing.
  3. Risk classifier for defense-in-depth logging of dangerous commands.
     This is NOT a command blacklist and is never the only barrier.

Restricted mode is ON by default for remote (non-owner) sessions.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any

PBKDF2_ITERATIONS = 240_000
TOKEN_TTL_SECONDS = 60 * 60 * 24 * 14  # 14 days

ROLES = ("owner", "editor", "viewer")


# ---------------------------------------------------------------------------
# passwords & tokens
# ---------------------------------------------------------------------------
def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return dk.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return hmac.compare_digest(dk.hex(), password_hash)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_room_code() -> str:
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no I/L/O/0/1
    parts = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)]
    return "-".join(parts)


# ---------------------------------------------------------------------------
# environment scrubbing
# ---------------------------------------------------------------------------
SENSITIVE_ENV_PATTERNS = re.compile(
    r"(KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|PRIVATE)", re.IGNORECASE
)


def scrub_env(base_env: dict[str, str] | None = None, extra_path: str | None = None,
              restricted: bool = True) -> dict[str, str]:
    env = dict(base_env or os.environ)
    # Drop secrets an operator may have in their shell env; remote shells get a clean one.
    if restricted:
        for key in list(env):
            if SENSITIVE_ENV_PATTERNS.search(key):
                env.pop(key, None)
    if extra_path:
        env["PATH"] = extra_path + os.pathsep + env.get("PATH", "")
        env["Path"] = env["PATH"]
    # Make restricted state visible to the shell (useful for PS prompts / bash rc).
    env["PENGUINTERM_RESTRICTED"] = "1" if restricted else "0"
    return env


# ---------------------------------------------------------------------------
# PATH shims (defense-in-depth for external commands)
# ---------------------------------------------------------------------------
# Names intercepted for remote/restricted sessions. Only real binaries can be
# intercepted — shell builtins (cmd's `del`, PowerShell cmdlets) cannot, which
# is exactly why this is defense-in-depth and not the primary control.
_SHIM_NAMES_UNIX = [
    "sudo", "su", "doas", "pkexec", "shutdown", "reboot", "poweroff", "halt",
    "mkfs", "mkfs.ext4", "mkfs.xfs", "mkfs.btrfs", "fdisk", "parted",
    "wipefs", "dd", "init", "systemctl", "service", "chroot", "nsenter",
    "unshare", "mount", "umount", "iptables", "nft", "sysctl", "modprobe",
    "insmod", "rmmod",
]
_SHIM_NAMES_WINDOWS = [
    "format.com", "diskpart.exe", "shutdown.exe", "cipher.exe", "bcdedit.exe",
    "vssadmin.exe", "wbadmin.exe", "fsutil.exe", "takeown.exe", "icacls.exe",
    "net.exe", "net1.exe", "sc.exe", "reg.exe", "bcdedit.exe",
]


def ensure_shims(shim_dir: Path) -> None:
    """Create refusal shims. Idempotent; safe to call at each session start."""
    shim_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        for name in _SHIM_NAMES_WINDOWS:
            p = shim_dir / name
            if not p.exists():
                p.write_text(
                    "@echo off\r\n"
                    "echo [PenguinTerm] '%~n0' is blocked for restricted remote sessions. 1>&2\r\n"
                    "echo Ask the server owner to grant elevated privileges if this is a mistake. 1>&2\r\n"
                    "exit /b 126\r\n",
                    encoding="utf-8",
                )
    else:
        for name in _SHIM_NAMES_UNIX:
            p = shim_dir / name
            if not p.exists():
                p.write_text(
                    "#!/bin/sh\n"
                    f"echo \"[PenguinTerm] '{name}' is blocked for restricted remote sessions.\" 1>&2\n"
                    "echo \"Ask the server owner to grant elevated privileges if this is a mistake.\" 1>&2\n"
                    "exit 126\n",
                    encoding="utf-8",
                )
                os.chmod(p, 0o755)


# ---------------------------------------------------------------------------
# cwd jail
# ---------------------------------------------------------------------------
def remote_start_cwd(cfg_get) -> str:
    jail = cfg_get("remote_cwd_jail", "home")
    if jail == "path":
        custom = cfg_get("remote_cwd_jail_path", "")
        if custom and Path(custom).is_dir():
            return custom
    if jail == "none":
        return str(Path.home())
    return str(Path.home())


# ---------------------------------------------------------------------------
# Linux resource limits (preexec_fn for pty fork)
# ---------------------------------------------------------------------------
def apply_linux_limits() -> None:  # pragma: no cover - runs in forked child
    try:
        import resource
        # CPU: 2 hours of CPU time per remote process; memory ceiling 2 GiB;
        # file size ceiling 1 GiB; at most 256 processes.
        resource.setrlimit(resource.RLIMIT_CPU, (7200, 7200))
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024**3, 1024**3))
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))
        except (ValueError, OSError):
            pass
        os.umask(0o077)
    except Exception:
        pass  # never let limit setup kill the child


# ---------------------------------------------------------------------------
# Windows Job Object limits (best effort, ctypes)
# ---------------------------------------------------------------------------
def apply_windows_job_object(pid: int) -> Any:
    """Assign a process to a job object with process/memory caps.

    Returns the job handle (keep it alive for the job's lifetime) or None.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(f, ctypes.c_ulonglong) for f in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
        JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_JOB_MEMORY
            | JOB_OBJECT_LIMIT_ACTIVE_PROCESS | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        info.BasicLimitInformation.ActiveProcessLimit = 32
        info.ProcessMemoryLimit = 2 * 1024**3
        info.JobMemoryLimit = 4 * 1024**3
        if not kernel32.SetInformationJobObject(
            job, 9, ctypes.byref(info), ctypes.sizeof(info)  # JobObjectExtendedLimitInformation
        ):
            kernel32.CloseHandle(job)
            return None
        # PROCESS_SET_QUOTA (0x0100) | PROCESS_TERMINATE (0x0001) |
        # PROCESS_QUERY_LIMITED_INFORMATION (0x1000) — documented as the minimum
        # for AssignProcessToJobObject; no VM read/write is requested.
        process = kernel32.OpenProcess(0x0100 | 0x0001 | 0x1000, False, pid)
        if process:
            kernel32.AssignProcessToJobObject(job, process)
            kernel32.CloseHandle(process)
        return job
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Risk classifier (defense-in-depth logging only — NOT a security boundary)
# ---------------------------------------------------------------------------
_RISK_PATTERNS = [
    re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\b[^\n]*(/\s*|/\*|~\s*|\.\s*$)"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\b(shutdown|reboot|poweroff|halt)\b"),
    re.compile(r"\bmkfs(\.[a-z0-9]+)?\b"),
    re.compile(r"\bdd\b[^\n]*\bof=/dev/(sd|nvme|hd|vd|mmcblk)"),
    re.compile(r"\b(format|diskpart)\b", re.IGNORECASE),
    re.compile(r"\bRemove-Item\b[^\n]*-Recurse[^\n]*(C:\\|\\|/)\s*$", re.IGNORECASE),
    re.compile(r"\b(Stop-Computer|Restart-Computer)\b", re.IGNORECASE),
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;"),  # fork bomb
]


def classify_command(text: str) -> str | None:
    """Return a short risk label if the input line looks dangerous, else None."""
    for pattern in _RISK_PATTERNS:
        if pattern.search(text):
            return pattern.pattern[:60]
    return None


# ---------------------------------------------------------------------------
# filesystem path safety for transfers
# ---------------------------------------------------------------------------
def safe_join(root: Path, relative: str) -> Path | None:
    """Resolve `relative` inside `root`, refusing escapes. Returns None on escape."""
    try:
        root_resolved = root.resolve()
        candidate = (root_resolved / relative).resolve()
        if candidate == root_resolved or root_resolved in candidate.parents:
            return candidate
        return None
    except (OSError, ValueError):
        return None


def is_probably_text(name: str) -> bool:
    return Path(name).suffix.lower() in {
        ".txt", ".md", ".json", ".toml", ".yaml", ".yml", ".py", ".js", ".ts",
        ".html", ".css", ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd",
        ".cfg", ".ini", ".log", ".csv", ".xml", ".sql",
    }
