"""Shared pytest fixtures.

Qt tests run on the offscreen platform so they need no display. The ``gdb_session_factory`` fixture
spins up :class:`GdbMiSession` instances wired to the fake-gdb subprocess and tears them down.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

# Must be set before any PySide6 import so Qt tests work headless.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from alynprog.backends.bmp.backend import BmpBackend
from alynprog.core.backend import ConnectOptions, ProbeInfo
from alynprog.transports.gdbmi import GdbMiSession
from tests.fake_gdb import FAKE_GDB


def fake_gdb_argv(profile: str = "bmp-v2") -> list[str]:
    return [sys.executable, str(FAKE_GDB), "--profile", profile]


@pytest.fixture
def temp_settings(tmp_path):
    """An isolated Settings backed by an ini file in a temp dir (no real registry/plist writes)."""
    from PySide6.QtCore import QSettings

    from alynprog.core.settings import Settings

    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    qs = QSettings(
        QSettings.Format.IniFormat, QSettings.Scope.UserScope, "AlynProgTest", "AlynProgTest"
    )
    return Settings(qs)


@pytest.fixture
def gdb_session_factory() -> Callable[..., GdbMiSession]:
    """Return a factory that creates & starts GdbMiSessions backed by fake-gdb."""
    created: list[GdbMiSession] = []

    def factory(profile: str = "bmp-v2", log_callback=None) -> GdbMiSession:
        session = GdbMiSession(
            "fake-gdb",
            argv_override=fake_gdb_argv(profile),
            log_callback=log_callback,
        )
        session.start()
        created.append(session)
        return session

    yield factory

    for session in created:
        session.stop()


@pytest.fixture
def make_bmp_backend() -> Callable[..., BmpBackend]:
    """Return a factory that builds a BmpBackend wired to fake-gdb and (by default) connects it."""
    created: list[BmpBackend] = []

    def factory(
        profile: str = "bmp-v2",
        *,
        connect: bool = True,
        options: ConnectOptions | None = None,
        log_callback=None,
    ) -> BmpBackend:
        session = GdbMiSession(
            "fake", argv_override=fake_gdb_argv(profile), log_callback=log_callback
        )
        backend = BmpBackend(gdb_path="fake", session=session)
        if connect:
            probe = ProbeInfo(backend="bmp", identifier="fake-port", display_name="fake")
            backend.connect(probe, options or ConnectOptions())
        created.append(backend)
        return backend

    yield factory

    for backend in created:
        backend.close()
