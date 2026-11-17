"""Unit tests for security primitives (no server required)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import security


def test_password_roundtrip():
    digest, salt = security.hash_password("correct horse battery staple")
    assert security.verify_password("correct horse battery staple", digest, salt)
    assert not security.verify_password("wrong password", digest, salt)


def test_password_unique_salts():
    d1, s1 = security.hash_password("same-password")
    d2, s2 = security.hash_password("same-password")
    assert s1 != s2 and d1 != d2


def test_room_code_format():
    code = security.new_room_code()
    parts = code.split("-")
    assert len(parts) == 3 and all(len(p) == 4 for p in parts)
    # ambiguous characters are excluded
    assert not set(code) & set("ILO01")


def test_scrub_env_removes_secrets():
    env = {"PATH": "/usr/bin", "AWS_SECRET_ACCESS_KEY": "leak", "EDITOR": "vim"}
    scrubbed = security.scrub_env(env, restricted=True)
    assert "AWS_SECRET_ACCESS_KEY" not in scrubbed
    assert scrubbed["EDITOR"] == "vim"
    assert scrubbed["PENGUINTERM_RESTRICTED"] == "1"


def test_scrub_env_prepends_shims():
    env = {"PATH": "/usr/bin"}
    scrubbed = security.scrub_env(env, extra_path="/shim", restricted=True)
    assert scrubbed["PATH"].startswith("/shim" + os.pathsep)


def test_classify_command_flags_danger():
    assert security.classify_command("rm -rf /") is not None
    assert security.classify_command("rm -rf /*") is not None
    assert security.classify_command("sudo systemctl stop x") is not None
    assert security.classify_command("mkfs.ext4 /dev/sda") is not None
    assert security.classify_command("shutdown -h now") is not None
    assert security.classify_command("ls -la") is None
    assert security.classify_command("echo hello world") is None


def test_safe_join(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    inside = security.safe_join(root, "sub/file.txt")
    assert inside is not None and inside.is_relative_to(root.resolve())
    assert security.safe_join(root, "../escape.txt") is None
    assert security.safe_join(root, "/absolute/outside.txt") is None
    assert security.safe_join(root, "sub/../../escape.txt") is None


def test_ensure_shims(tmp_path):
    target = tmp_path / "shims"
    security.ensure_shims(target)
    names = os.listdir(target)
    assert ("sudo" in names) or ("shutdown.exe" in names)
    # idempotent
    security.ensure_shims(target)
