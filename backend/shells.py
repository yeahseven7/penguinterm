"""Detection of installed shells on Windows and Linux.

Returns a list of shell descriptors the frontend renders in the "new tab"
menu. Detection is best-effort: anything found on PATH or in well-known
locations is offered; nothing is assumed to exist.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import TypedDict


class ShellInfo(TypedDict):
    id: str
    name: str
    path: str
    kind: str  # 'powershell' | 'cmd' | 'posix' | 'wsl'
    args: list[str]
    icon: str  # 'windows' | 'linux' | 'shell'


def _which(name: str) -> str | None:
    return shutil.which(name)


def detect_shells() -> list[ShellInfo]:
    if sys.platform == "win32":
        return _detect_windows()
    return _detect_posix()


def _detect_windows() -> list[ShellInfo]:
    shells: list[ShellInfo] = []
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")

    # PowerShell 7+
    pwsh = _which("pwsh.exe") or _which("pwsh")
    if not pwsh:
        cand = os.path.join(pf, "PowerShell", "7", "pwsh.exe")
        pwsh = cand if os.path.exists(cand) else None
    if pwsh:
        shells.append(ShellInfo(id="pwsh", name="PowerShell 7", path=pwsh,
                                kind="powershell", args=["-NoLogo"], icon="windows"))

    # Windows PowerShell 5.1
    winps = shutil.which("powershell.exe") or os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        r"System32\WindowsPowerShell\v1.0\powershell.exe")
    if os.path.exists(winps):
        shells.append(ShellInfo(id="powershell", name="Windows PowerShell", path=winps,
                                kind="powershell", args=["-NoLogo"], icon="windows"))

    cmd = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), r"System32\cmd.exe")
    if os.path.exists(cmd):
        shells.append(ShellInfo(id="cmd", name="Command Prompt", path=cmd,
                                kind="cmd", args=[], icon="windows"))

    # WSL2 distros (optional integration)
    wsl = _which("wsl.exe")
    if wsl:
        try:
            out = subprocess.run([wsl, "-l", "-q"], capture_output=True,
                                 timeout=8, creationflags=0x08000000)  # CREATE_NO_WINDOW
            raw = out.stdout.decode("utf-16-le", errors="ignore")
            for line in raw.splitlines():
                distro = line.strip().strip("\x00").strip()
                if distro and not distro.lower().startswith(("windows", "docker")):
                    shells.append(ShellInfo(
                        id=f"wsl:{distro}", name=f"WSL: {distro}", path=wsl,
                        kind="wsl", args=["-d", distro], icon="linux"))
        except Exception:
            pass

    # Git Bash if installed
    for cand in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if os.path.exists(cand):
            shells.append(ShellInfo(id="gitbash", name="Git Bash", path=cand,
                                    kind="posix", args=["--login", "-i"], icon="linux"))
            break
    return shells


def _detect_posix() -> list[ShellInfo]:
    shells: list[ShellInfo] = []
    seen: set[str] = set()

    def add(sid: str, name: str, path: str, args: list[str]):
        key = os.path.realpath(path)
        if key not in seen:
            seen.add(key)
            shells.append(ShellInfo(id=sid, name=name, path=path,
                                    kind="posix", args=args, icon="linux"))

    env_shell = os.environ.get("SHELL")
    if env_shell and os.path.exists(env_shell):
        base = os.path.basename(env_shell)
        add(base, f"{base} (default)", env_shell, ["-i"] if base in ("bash", "zsh", "sh") else ["-i"])

    for name in ("bash", "zsh", "fish", "sh", "dash", "ksh", "tcsh"):
        path = _which(name)
        if path:
            args = ["-i"] if name != "fish" else ["-i"]
            add(name, name.capitalize() if name not in ("zsh",) else "Zsh", path, args)

    try:
        with open("/etc/shells", "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                path = line.strip()
                if not path or path.startswith("#") or not os.path.isfile(path):
                    continue
                base = os.path.basename(path)
                if base in ("bash", "zsh", "fish", "sh", "dash", "ksh", "csh", "tcsh"):
                    add(base, base, path, ["-i"])
    except OSError:
        pass
    return shells


def default_shell(shells: list[ShellInfo]) -> str | None:
    if not shells:
        return None
    prefer = ("powershell" if sys.platform == "win32" else os.path.basename(os.environ.get("SHELL", ""))) or "pwsh"
    for shell in shells:
        if shell["id"].startswith(prefer) or shell["kind"] == prefer:
            return shell["id"]
    return shells[0]["id"]
