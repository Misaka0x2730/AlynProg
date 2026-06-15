"""Themed line icons for the left navigation rail.

The SVGs are authored with ``stroke="currentColor"`` so one source recolours to whatever the active
palette needs. We substitute the colour and render through :class:`QSvgRenderer`, supersampling so
the thin strokes stay crisp when the view asks for the icon at 2x/3x device-pixel ratios, and we
bake a *Normal* and a *Selected* variant into each :class:`QIcon` so the glyph stays legible both on
the rail background and on the selection highlight. Callers rebuild the icons on a palette change
(light/dark) so they always match the surrounding text colour.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# How much larger than the logical icon size we rasterise; downscaled by QIcon for the actual slot.
_SUPERSAMPLE = 4

MEMORY_SVG = """<svg
    xmlns="http://www.w3.org/2000/svg"
    width="24"
    height="24"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.75"
    stroke-linecap="round"
    stroke-linejoin="round">

    <rect x="5" y="7" width="11" height="11" rx="2"/>

    <path d="M3.5 9H5"/>
    <path d="M3.5 12.5H5"/>
    <path d="M3.5 16H5"/>
    <path d="M8 18v1.5"/>
    <path d="M11 18v1.5"/>
    <path d="M14 18v1.5"/>

    <path d="M8 11h5"/>
    <path d="M8 14h4"/>

    <path d="M14.5 4.5H18l3 3V16"/>
    <path d="M18 4.5V7a1 1 0 0 0 1 1h2"/>
</svg>"""

PROGRAMMING_SVG = """<svg
    xmlns="http://www.w3.org/2000/svg"
    width="24"
    height="24"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.75"
    stroke-linecap="round"
    stroke-linejoin="round">

    <rect x="8" y="7" width="11" height="11" rx="2"/>

    <path d="M19 9h1.5"/>
    <path d="M19 12.5h1.5"/>
    <path d="M19 16h1.5"/>
    <path d="M11 18v1.5"/>
    <path d="M14 18v1.5"/>
    <path d="M17 18v1.5"/>

    <path d="M3.5 12.5h9"/>
    <path d="M9.5 9.5l3 3-3 3"/>
</svg>"""


def _render(svg: str, color: QColor, size: int) -> QPixmap:
    data = svg.replace("currentColor", color.name())
    renderer = QSvgRenderer(QByteArray(data.encode("utf-8")))
    pixmap = QPixmap(size * _SUPERSAMPLE, size * _SUPERSAMPLE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return pixmap


def nav_icon(svg: str, normal: QColor, selected: QColor, size: int = 26) -> QIcon:
    """A two-mode icon for the nav rail: *normal* tint when idle, *selected* on the active row."""
    icon = QIcon()
    icon.addPixmap(_render(svg, normal, size), QIcon.Mode.Normal)
    icon.addPixmap(_render(svg, selected, size), QIcon.Mode.Selected)
    return icon
