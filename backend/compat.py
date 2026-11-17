"""Optional cross-platform command compatibility layer (spec §9).

Two independent mechanisms, both OFF by default, never replacing the native
shell:

  1. Line translation — when the client submits a complete single-line
     command (e.g. paste ending in Enter) and this layer is enabled, the
     first token is translated when the target platform lacks the alias:

         Linux -> Windows:  ls->dir  clear->cls  rm->del  cp->copy  mv->move
         Windows -> Linux:  dir->ls  cls->clear  del->rm  copy->cp  move->mv

     Flags are NOT translated. Native commands always win; only the first
     token of a complete line is touched; partial/interactive input is
     passed through untouched.

  2. Compat shims — a directory of tiny wrapper scripts (`ls` -> `dir` on
     Windows, `dir` -> `ls` on Linux, ...) prepended to PATH for sessions
     when the layer is enabled. Covers bare commands typed interactively.
     Shell builtins and aliases take precedence over PATH, which is the
     desired behavior.

This layer is deliberately minimal. It is a convenience for muscle memory,
not an emulator and never a security boundary.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

LINUX_TO_WINDOWS = {
    "ls": "dir", "clear": "cls", "rm": "del", "cp": "copy", "mv": "move",
    "cat": "type", "touch": "type nul >", "pwd": "cd",
}
WINDOWS_TO_LINUX = {
    "dir": "ls", "cls": "clear", "del": "rm", "copy": "cp", "move": "mv",
    "type": "cat",
}

_LINE_RE = re.compile(r"^([A-Za-z][\w.-]*)(.*)$")


def translate_line(line: str, platform: str | None = None) -> tuple[str, str | None]:
    """Translate the first token of a complete command line if applicable.

    Returns (possibly new line, human-readable note or None).
    """
    platform = platform or sys.platform
    mapping = LINUX_TO_WINDOWS if platform == "win32" else WINDOWS_TO_LINUX
    match = _LINE_RE.match(line.strip())
    if not match:
        return line, None
    token, rest = match.groups()
    replacement = mapping.get(token.lower())
    if not replacement or replacement == token:
        return line, None
    return replacement + rest, f"{token} -> {replacement}"


def ensure_compat_shims(shim_dir: Path) -> None:
    """Create compat shims. Idempotent."""
    shim_dir.mkdir(parents=True, exist_ok=True)
    mapping = LINUX_TO_WINDOWS if sys.platform == "win32" else WINDOWS_TO_LINUX
    if sys.platform == "win32":
        for name, target in mapping.items():
            root = target.split()[0]
            path = shim_dir / f"{name}.cmd"
            if not path.exists():
                path.write_text(
                    f"@echo off\r\nrem PenguinTerm compat shim: {name} -> {target}\r\n"
                    f"{root} %*\r\n",
                    encoding="utf-8",
                )
    else:
        for name, target in mapping.items():
            root = target.split()[0]
            path = shim_dir / name
            if not path.exists():
                path.write_text(
                    f"#!/bin/sh\n# PenguinTerm compat shim: {name} -> {target}\n"
                    f'exec {root} "$@"\n',
                    encoding="utf-8",
                )
                os.chmod(path, 0o755)
