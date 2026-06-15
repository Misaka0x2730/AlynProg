"""The shared content-font size for every hex pane.

All hex views — the live device memory, each firmware-file tab and both halves of a compare tab —
render their bytes in the same monospace font. :class:`HexFontController` is the single source of
truth for that font's point size: it loads the persisted value (or derives a readable default from
the system fixed font), clamps every change to a sane range, writes it back to :class:`Settings`,
and broadcasts :attr:`sizeChanged` so all open panes re-font themselves live. The status-bar slider
and the View-menu zoom actions drive it; the panes only listen.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QFont, QFontDatabase

from alynprog.core.settings import Settings

# The slider/zoom range, in points. The floor keeps the table legible; the ceiling keeps a row from
# dwarfing the viewport.
MIN_POINT_SIZE = 8
MAX_POINT_SIZE = 32

# The size used until the user picks their own: a comfortable default for the dense hex grid (the
# bare platform fixed font rendered too small).
DEFAULT_POINT_SIZE = 14


def fixed_font(point_size: int) -> QFont:
    """The platform monospace font (aligned hex/ASCII columns) at *point_size* points."""
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSize(point_size)
    return font


def clamp_point_size(point_size: int) -> int:
    """Confine *point_size* to the supported ``[MIN_POINT_SIZE, MAX_POINT_SIZE]`` range."""
    return max(MIN_POINT_SIZE, min(MAX_POINT_SIZE, point_size))


def default_point_size() -> int:
    """The starting size when the user has never chosen one."""
    return clamp_point_size(DEFAULT_POINT_SIZE)


class HexFontController(QObject):
    """Holds and persists the shared hex-view font size; emits :attr:`sizeChanged` on a change."""

    sizeChanged = Signal(int)  # the new point size

    def __init__(self, settings: Settings | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        stored = settings.hex_font_point_size if settings is not None else 0
        self._point_size = clamp_point_size(stored) if stored > 0 else default_point_size()

    @property
    def point_size(self) -> int:
        return self._point_size

    def set_point_size(self, point_size: int) -> None:
        """Set the shared size (clamped). Persists and broadcasts only when the value changes."""
        point_size = clamp_point_size(int(point_size))
        if point_size == self._point_size:
            return
        self._point_size = point_size
        if self._settings is not None:
            self._settings.hex_font_point_size = point_size
        self.sizeChanged.emit(point_size)

    def step(self, delta: int) -> None:
        """Nudge the size by *delta* points (used by the zoom-in / zoom-out actions)."""
        self.set_point_size(self._point_size + delta)

    def reset(self) -> None:
        """Return to the derived default size."""
        self.set_point_size(default_point_size())
