"""Runtime light/dark/system theming built on Qt's native color-scheme support.

No third-party theme packages are used. We rely on the Fusion style (consistent across platforms)
plus ``QStyleHints.setColorScheme`` / ``unsetColorScheme`` (Qt 6.8+) so that "System" mode follows
the OS appearance live via the ``colorSchemeChanged`` signal.
"""

from __future__ import annotations

import enum

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication


class ThemeMode(enum.Enum):
    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"

    @classmethod
    def from_value(cls, value: str | None, default: ThemeMode) -> ThemeMode:
        if value is None:
            return default
        try:
            return cls(value)
        except ValueError:
            return default


_SCHEME = {
    ThemeMode.LIGHT: Qt.ColorScheme.Light,
    ThemeMode.DARK: Qt.ColorScheme.Dark,
}


class ThemeManager(QObject):
    """Applies and tracks the application color scheme.

    Emits :attr:`changed` (with the active :class:`ThemeMode`) whenever the mode is applied, and
    emits :attr:`scheme_changed` (with ``"light"`` / ``"dark"``) whenever the *effective* scheme
    changes — including OS-driven changes while in System mode.
    """

    changed = Signal(object)
    scheme_changed = Signal(str)

    def __init__(self, app: QApplication, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._app = app
        self._mode = ThemeMode.SYSTEM
        self._last_scheme: str | None = None
        # Fusion gives us a palette that honours setColorScheme on every platform.
        app.setStyle("Fusion")
        hints = QGuiApplication.styleHints()
        hints.colorSchemeChanged.connect(self._on_os_scheme_changed)

    @property
    def mode(self) -> ThemeMode:
        return self._mode

    def effective_scheme(self) -> str:
        """Return the scheme actually in effect: ``"light"`` or ``"dark"``."""
        scheme = QGuiApplication.styleHints().colorScheme()
        return "dark" if scheme == Qt.ColorScheme.Dark else "light"

    def apply(self, mode: ThemeMode) -> None:
        self._mode = mode
        hints = QGuiApplication.styleHints()
        if mode is ThemeMode.SYSTEM:
            hints.unsetColorScheme()
        else:
            hints.setColorScheme(_SCHEME[mode])
        self.changed.emit(mode)
        self._maybe_emit_scheme()

    def _on_os_scheme_changed(self, _scheme: Qt.ColorScheme) -> None:
        # Only meaningful while we are deferring to the OS, but harmless otherwise.
        self._maybe_emit_scheme()

    def _maybe_emit_scheme(self) -> None:
        scheme = self.effective_scheme()
        if scheme != self._last_scheme:
            self._last_scheme = scheme
            self.scheme_changed.emit(scheme)
