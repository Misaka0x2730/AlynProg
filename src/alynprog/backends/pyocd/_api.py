"""Lazy facade over the optional pyOCD library.

Nothing in :mod:`alynprog.backends.pyocd` imports pyocd at module load time; the import happens
inside :meth:`PyocdApi.real`, so the package stays importable when the optional dependency is not
installed. Tests inject a fake :class:`PyocdApi` to exercise the backend without real hardware.
"""

from __future__ import annotations

import contextlib
import importlib.util
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from alynprog.core.errors import NotSupportedError


def pyocd_available() -> bool:
    """True if the optional ``pyocd`` package is importable, without importing it."""
    try:
        return importlib.util.find_spec("pyocd") is not None
    except (ImportError, ValueError):
        # ValueError: a blocked/None entry in sys.modules; ImportError: a missing parent package.
        return False


def prewarm() -> None:
    """Best-effort eager import of pyOCD's heavy modules (~2 s). Call off the GUI thread.

    Used at startup so the first probe discovery / target dialog does not pay the import cost on
    the GUI thread. Silently does nothing if pyOCD is absent or fails to import.
    """
    with contextlib.suppress(Exception):
        PyocdApi.real()


@dataclass(frozen=True)
class PyocdApi:
    """The pyOCD entry points the backend uses, bundled so tests can inject fakes.

    Built for production by :meth:`real`; in tests, constructed directly with stand-ins that mimic
    the small slice of pyOCD's surface the backend relies on.
    """

    get_all_connected_probes: Callable[..., list[Any]]
    session_factory: Callable[..., Any]
    file_programmer: type
    flash_eraser: type
    eraser_mode: type
    list_targets: Callable[..., dict[str, Any]]
    exceptions: ModuleType

    @staticmethod
    def real() -> PyocdApi:
        """Bind a :class:`PyocdApi` to the installed pyOCD, or raise if it is absent."""
        try:
            from pyocd.core import exceptions
            from pyocd.core.helpers import ConnectHelper
            from pyocd.core.session import Session
            from pyocd.flash.eraser import FlashEraser
            from pyocd.flash.file_programmer import FileProgrammer
            from pyocd.tools.lists import ListGenerator
        except ImportError as exc:
            raise NotSupportedError(
                "pyOCD support is not installed — install it with: pip install 'alynprog[pyocd]'"
            ) from exc
        return PyocdApi(
            get_all_connected_probes=ConnectHelper.get_all_connected_probes,
            session_factory=Session,
            file_programmer=FileProgrammer,
            flash_eraser=FlashEraser,
            eraser_mode=FlashEraser.Mode,
            list_targets=ListGenerator.list_targets,
            exceptions=exceptions,
        )
