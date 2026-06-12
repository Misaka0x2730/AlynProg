"""Unit tests for GDB locator (validation + PATH search via a fake gdb script)."""

from __future__ import annotations

import os
import stat
import sys

from alynprog.tools.gdb_locator import find_gdb, validate_gdb

_FAKE_GDB_BODY = """#!{python}
import sys
if "--version" in sys.argv:
    print("GNU gdb (Fake Toolchain) 16.3")
    sys.exit(0)
sys.exit(1)
"""

_NOT_GDB_BODY = """#!{python}
print("definitely not a debugger")
"""


def _make_exe(path, body):
    path.write_text(body.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def test_validate_accepts_gdb_like_output(tmp_path):
    exe = _make_exe(tmp_path / "arm-none-eabi-gdb", _FAKE_GDB_BODY)
    info = validate_gdb(exe)
    assert info is not None
    assert "gdb" in info.version.lower()


def test_validate_rejects_non_gdb(tmp_path):
    exe = _make_exe(tmp_path / "notgdb", _NOT_GDB_BODY)
    assert validate_gdb(exe) is None


def test_validate_rejects_missing_file(tmp_path):
    assert validate_gdb(tmp_path / "absent") is None


def test_explicit_path_is_honoured(tmp_path):
    exe = _make_exe(tmp_path / "my-gdb", _FAKE_GDB_BODY)
    info = find_gdb(str(exe))
    assert info is not None and info.path == str(exe)


def test_explicit_invalid_path_does_not_fall_back(tmp_path, monkeypatch):
    # Even if PATH has a valid gdb, an explicit-but-invalid path is a hard miss.
    good_dir = tmp_path / "bin"
    good_dir.mkdir()
    _make_exe(good_dir / "arm-none-eabi-gdb", _FAKE_GDB_BODY)
    monkeypatch.setenv("PATH", str(good_dir))
    assert find_gdb(str(tmp_path / "bogus")) is None


def test_find_on_path(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_exe(bin_dir / "arm-none-eabi-gdb", _FAKE_GDB_BODY)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep)
    info = find_gdb()
    assert info is not None
    assert info.path.endswith("arm-none-eabi-gdb")
