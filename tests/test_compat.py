"""Unit tests for the compatibility layer."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.compat import translate_line, LINUX_TO_WINDOWS, WINDOWS_TO_LINUX


def test_linux_to_windows():
    line, note = translate_line("ls -la", "win32")
    assert line == "dir -la" and note == "ls -> dir"
    line, note = translate_line("clear", "win32")
    assert line == "cls"
    line, note = translate_line("rm -r build", "win32")
    assert line == "del -r build"


def test_windows_to_linux():
    line, note = translate_line("dir /w", "linux")
    assert line == "ls /w"
    line, note = translate_line("cls", "linux")
    assert line == "clear"
    line, note = translate_line("del foo.txt", "linux")
    assert line == "rm foo.txt"


def test_native_commands_untouched():
    assert translate_line("git status", "win32")[0] == "git status"
    assert translate_line("python script.py", "linux")[0] == "python script.py"


def test_multiline_first_token_only():
    line, note = translate_line("mv a b c", "linux")
    assert line == "mv a b c"  # mv is native on linux


def test_empty_input():
    assert translate_line("", "win32") == ("", None)
    assert translate_line("!!!", "win32") == ("!!!", None)


def test_shim_tables_are_sane():
    # both tables only contain single-token replacements or documented prefixes
    for table in (LINUX_TO_WINDOWS, WINDOWS_TO_LINUX):
        for key, value in table.items():
            assert key == key.lower()
            assert value.strip()
