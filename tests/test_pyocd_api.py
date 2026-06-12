"""M1: the lazy pyOCD facade — import-safe without pyocd, ``real()`` binds entry points with it."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

import alynprog.backends.pyocd._api as api_mod
from alynprog.backends.pyocd import PyocdApi, pyocd_available


def test_pyocd_available_is_bool():
    assert isinstance(pyocd_available(), bool)


def test_pyocd_available_false_when_find_spec_none(monkeypatch):
    monkeypatch.setattr(api_mod.importlib.util, "find_spec", lambda name: None)
    assert api_mod.pyocd_available() is False


def test_pyocd_available_true_when_find_spec_present(monkeypatch):
    monkeypatch.setattr(api_mod.importlib.util, "find_spec", lambda name: object())
    assert api_mod.pyocd_available() is True


def test_package_is_import_safe_without_pyocd():
    """In a subprocess where ``import pyocd`` fails, the package still imports and degrades cleanly.

    ``sys.modules['pyocd'] = None`` makes any ``import pyocd`` raise ImportError, simulating a
    checkout that never installed the optional extra.
    """
    code = textwrap.dedent(
        """
        import sys
        sys.modules["pyocd"] = None  # any 'import pyocd' now raises ImportError

        import alynprog.backends.pyocd as pkg
        import alynprog.backends.pyocd.errors  # must import without pyocd
        from alynprog.backends.pyocd.errors import mapped_errors
        from alynprog.core.errors import NotSupportedError

        assert pkg.pyocd_available() is False

        try:
            pkg.PyocdApi.real()
        except NotSupportedError:
            pass
        else:
            raise AssertionError("real() must raise NotSupportedError when pyocd is unavailable")

        # Non-pyocd errors still pass through mapped_errors untouched even with pyocd gone.
        try:
            with mapped_errors("op"):
                raise KeyError("x")
        except KeyError:
            pass
        else:
            raise AssertionError("mapped_errors must not swallow non-pyocd errors")

        print("IMPORT_SAFE_OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "IMPORT_SAFE_OK" in result.stdout


@pytest.mark.skipif(not pyocd_available(), reason="pyocd not installed")
def test_real_binds_entry_points():
    api = PyocdApi.real()
    assert callable(api.get_all_connected_probes)
    assert callable(api.session_factory)
    assert callable(api.list_targets)
    assert callable(api.file_programmer)
    assert callable(api.flash_eraser)
    assert [m.name for m in api.eraser_mode] == ["MASS", "CHIP", "SECTOR"]
    assert hasattr(api.exceptions, "Error")
