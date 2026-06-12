"""The side-by-side comparison result tab.

When a file is compared against device memory the outcome opens here as its own tab: a horizontal
split with the **device memory** on the left and the **file** on the right, both addressed
identically and with the differing bytes tinted in each half. A header summarises the result and
steps through the differences (scrolling is locked between the two halves, so the rows stay
aligned).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from alynprog.core.backend import MemoryRegion, RegionKind
from alynprog.core.compare import MemoryCompareResult
from alynprog.core.settings import Settings
from alynprog.ui.panels.hexview.sources import BufferSource
from alynprog.ui.panels.hexview.view import HexPane


class _BufferPane(HexPane):
    """A read-only hex pane over a fixed set of (address, bytes) segments."""

    def __init__(
        self,
        segments: list[tuple[int, bytes]],
        settings: Settings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(settings, parent)
        self._source = BufferSource(segments, self)
        self._bind_source(self._source.request_page, self._source.write)
        self._source.page_ready.connect(self._model.fill_page)
        regions = [
            MemoryRegion(addr, len(data), RegionKind.UNKNOWN, name=f"Segment {i}")
            for i, (addr, data) in enumerate(segments)
        ]
        self._populate_regions(regions)

    def set_highlight(self, ranges: list[tuple[int, int]]) -> None:
        self._model.set_highlight_ranges(ranges)


class CompareTab(QWidget):
    def __init__(
        self,
        result: MemoryCompareResult,
        file_label: str,
        settings: Settings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._file_label = file_label
        device_segments = [(seg.addr, seg.actual) for seg in result.segments]
        file_segments = [(seg.addr, seg.expected) for seg in result.segments]
        highlights = [(d.addr, d.end) for seg in result.segments for d in seg.diffs]
        self._diff_addrs = sorted(d.addr for seg in result.segments for d in seg.diffs)
        self._diff_cursor = -1

        self._device = _BufferPane(device_segments, settings, self)
        self._file = _BufferPane(file_segments, settings, self)
        self._device.set_highlight(highlights)
        self._file.set_highlight(highlights)
        self._device.sync_with(self._file)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._titled(self.tr("Device memory"), self._device))
        splitter.addWidget(self._titled(file_label, self._file))
        splitter.setSizes([1, 1])

        layout = QVBoxLayout(self)
        layout.addLayout(self._build_summary(result))
        layout.addWidget(splitter, 1)

    @staticmethod
    def title_for(file_label: str) -> str:
        return f"Compare: {file_label}"

    # --- construction ----------------------------------------------------------

    def _titled(self, label: str, pane: QWidget) -> QWidget:
        column = QWidget(self)
        box = QVBoxLayout(column)
        box.setContentsMargins(0, 0, 0, 0)
        header = QLabel(label, column)
        header.setStyleSheet("font-weight: bold;")
        box.addWidget(header)
        box.addWidget(pane, 1)
        return column

    def _build_summary(self, result: MemoryCompareResult) -> QHBoxLayout:
        if result.matched:
            text = self.tr("Contents match (%d bytes)") % result.bytes_compared
        else:
            text = self.tr("%d byte(s) differ in %d range(s)") % (
                result.bytes_differing,
                result.range_count,
            )
            if result.truncated:
                text = self.tr("%s — list truncated") % text
        self._summary = QLabel(text, self)

        self._prev_btn = QPushButton(self.tr("Previous"), self)
        self._prev_btn.clicked.connect(self._prev_diff)
        self._next_btn = QPushButton(self.tr("Next difference"), self)
        self._next_btn.clicked.connect(self._next_diff)
        has_diffs = bool(self._diff_addrs)
        self._prev_btn.setEnabled(has_diffs)
        self._next_btn.setEnabled(has_diffs)

        row = QHBoxLayout()
        row.addWidget(self._summary, 1)
        row.addWidget(self._prev_btn)
        row.addWidget(self._next_btn)
        return row

    # --- diff navigation -------------------------------------------------------

    def _next_diff(self) -> None:
        if not self._diff_addrs:
            return
        self._diff_cursor = min(self._diff_cursor + 1, len(self._diff_addrs) - 1)
        self._device.goto_address(self._diff_addrs[self._diff_cursor])

    def _prev_diff(self) -> None:
        if not self._diff_addrs:
            return
        self._diff_cursor = max(self._diff_cursor - 1, 0)
        self._device.goto_address(self._diff_addrs[self._diff_cursor])
