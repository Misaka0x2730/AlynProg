"""A paged, async-filled table model over a memory region.

The model has a *fixed* row count derived from the region size (no ``canFetchMore`` — that is for
sequential growth, not random access over a known-size region). On a cell miss it asks the cache
for the page base to fetch and invokes a request callback; the worker later delivers the page via
:meth:`fill_page`, which fills the cache and emits ``dataChanged`` for the affected rows. Only RAM
and flash cells are editable (flash via a backend read-modify-write); every other region (ROM,
device/peripheral, unclassified) is read-only.
"""

from __future__ import annotations

import bisect
from collections.abc import Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from alynprog.core.backend import MemoryRegion, RegionKind
from alynprog.core.memory import PagedMemoryCache

BYTES_PER_ROW = 16

# Tint painted behind bytes that differ in a comparison (low alpha so the glyph stays readable).
DIFF_BACKGROUND = QColor(229, 57, 53, 80)

RequestCallback = Callable[[int, int], None]  # (page_base, size)
WriteCallback = Callable[[int, bytes], None]  # (addr, data)


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sort and merge ``[start, end)`` ranges into a minimal, non-overlapping ascending list."""
    ordered = sorted(ranges)
    merged: list[tuple[int, int]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            prev_start, prev_end = merged[-1]
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


class HexTableModel(QAbstractTableModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._region: MemoryRegion | None = None
        self._width = 1  # bytes per cell: 1, 2 or 4
        self._cache = PagedMemoryCache()
        self._memory_map: list[MemoryRegion] = []
        self._request: RequestCallback | None = None
        self._write: WriteCallback | None = None
        # When set, every in-region byte is editable, bypassing the device memory-map gating (used
        # by file-document panes, where the whole buffer is freely editable).
        self._editable_override = False
        # Byte ranges [start, end) painted as differing after a comparison: kept sorted and merged
        # so lookups can binary-search (a whole-region diff can hold many thousands of ranges).
        self._highlight: list[tuple[int, int]] = []
        self._highlight_starts: list[int] = []
        # ASCII highlight: which row and byte-offset range mirrors the selected hex cell.
        self._hl_row = -1
        self._hl_lo = 0
        self._hl_hi = 0

    # --- configuration ---------------------------------------------------------

    def set_request_callback(self, callback: RequestCallback) -> None:
        self._request = callback

    def set_write_callback(self, callback: WriteCallback) -> None:
        self._write = callback

    def set_memory_map(self, regions: list[MemoryRegion]) -> None:
        self._memory_map = list(regions)

    def set_editable_override(self, editable: bool) -> None:
        self._editable_override = editable

    # --- diff highlight (set after a comparison) -------------------------------

    def set_highlight_ranges(self, ranges: list[tuple[int, int]]) -> None:
        """Mark byte ranges ``[start, end)`` as differing; repaints the whole table."""
        self._highlight = _merge_ranges(ranges)
        self._highlight_starts = [start for start, _end in self._highlight]
        if self._region is not None:
            top = self.index(0, 0)
            bottom = self.index(self.rowCount() - 1, self.columnCount() - 1)
            self.dataChanged.emit(top, bottom, [Qt.ItemDataRole.BackgroundRole])

    def _is_highlighted(self, lo: int, hi: int) -> bool:
        # Ranges are sorted and non-overlapping, so the only candidate is the last one starting
        # before *hi*; if it reaches past *lo* the cell overlaps a difference.
        starts = self._highlight_starts
        if not starts:
            return False
        i = bisect.bisect_right(starts, hi - 1) - 1
        return i >= 0 and self._highlight[i][1] > lo

    def diff_char_ranges(self, row: int) -> list[tuple[int, int]]:
        """ASCII char-offset ranges ``[lo, hi)`` within *row* that are highlighted as differing.

        Used by the delegate to tint individual ASCII glyphs (the hex cells tint via
        ``BackgroundRole``; the ASCII column is custom-painted and asks for the offsets directly).
        """
        if not self._highlight or self._region is None:
            return []
        row_start = self._region.start + row * BYTES_PER_ROW
        row_end = min(row_start + BYTES_PER_ROW, self._region.end)
        i = bisect.bisect_left(self._highlight_starts, row_start)
        if i > 0 and self._highlight[i - 1][1] > row_start:
            i -= 1  # a range starting earlier may still reach into this row
        ranges: list[tuple[int, int]] = []
        count = len(self._highlight)
        while i < count and self._highlight[i][0] < row_end:
            start, end = self._highlight[i]
            lo = max(start, row_start)
            hi = min(end, row_end)
            if lo < hi:
                ranges.append((lo - row_start, hi - row_start))
            i += 1
        return ranges

    # --- ASCII highlight (mirrors the selected hex cell) -----------------------

    def ascii_column(self) -> int:
        return self._hex_columns()

    def ascii_highlight(self) -> tuple[int, int, int]:
        """Return (row, byte_lo, byte_hi) of the ASCII chars to highlight; row -1 means none."""
        return self._hl_row, self._hl_lo, self._hl_hi

    def set_ascii_highlight(self, row: int, lo: int, hi: int) -> None:
        old = self._hl_row
        self._hl_row, self._hl_lo, self._hl_hi = row, lo, hi
        if self._region is None:
            return
        col = self._hex_columns()
        for changed in {old, row}:
            if 0 <= changed < self.rowCount():
                index = self.index(changed, col)
                self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole])

    def set_region(self, region: MemoryRegion | None) -> None:
        # Diff highlights are kept in absolute addresses so they survive region/width changes (the
        # caller clears them explicitly); only bytes inside the current region are ever tinted.
        self.beginResetModel()
        self._region = region
        self._cache.invalidate()
        self._hl_row = -1
        self.endResetModel()

    def set_width(self, width: int) -> None:
        if width not in (1, 2, 4):
            return
        self.beginResetModel()
        self._width = width
        self._hl_row = -1
        self.endResetModel()

    @property
    def width(self) -> int:
        return self._width

    @property
    def region(self) -> MemoryRegion | None:
        return self._region

    def invalidate(self, addr: int | None = None, length: int | None = None) -> None:
        self._cache.invalidate(addr, length)
        if self._region is not None:
            top = self.index(0, 0)
            bottom = self.index(self.rowCount() - 1, self.columnCount() - 1)
            self.dataChanged.emit(top, bottom)

    def fill_page(self, base: int, data: bytes) -> None:
        self._cache.fill(base, data)
        if self._region is None:
            return
        first_row = max(0, (base - self._region.start) // BYTES_PER_ROW)
        last_row = min(
            self.rowCount() - 1, (base + len(data) - 1 - self._region.start) // BYTES_PER_ROW
        )
        if first_row <= last_row:
            top = self.index(first_row, 0)
            bottom = self.index(last_row, self.columnCount() - 1)
            self.dataChanged.emit(top, bottom)

    # --- Qt model interface ----------------------------------------------------

    def _hex_columns(self) -> int:
        return BYTES_PER_ROW // self._width

    def rowCount(self, parent=QModelIndex()) -> int:
        if parent.isValid() or self._region is None:
            return 0
        return (self._region.size + BYTES_PER_ROW - 1) // BYTES_PER_ROW

    def columnCount(self, parent=QModelIndex()) -> int:
        if parent.isValid() or self._region is None:
            return 0
        return self._hex_columns() + 1  # + ASCII column

    def headerData(
        self, section: int, orientation: Qt.Orientation, role=Qt.ItemDataRole.DisplayRole
    ):
        if role != Qt.ItemDataRole.DisplayRole or self._region is None:
            return None
        if orientation == Qt.Orientation.Vertical:
            return f"0x{self._region.start + section * BYTES_PER_ROW:08X}"
        if section == self._hex_columns():
            return self.tr("ASCII")
        return f"+{section * self._width:X}"

    def _cell_addr(self, row: int, col: int) -> int:
        return self._region.start + row * BYTES_PER_ROW + col * self._width

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or self._region is None:
            return None
        row, col = index.row(), index.column()
        if role == Qt.ItemDataRole.BackgroundRole:
            return self._background_for(row, col)
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return None
        if col == self._hex_columns():
            return self._ascii_for_row(row)
        return self._hex_cell(row, col)

    def _background_for(self, row: int, col: int):
        # The ASCII column is custom-painted and tints individual glyphs via diff_char_ranges();
        # only the hex cells use BackgroundRole here.
        if not self._highlight or col == self._hex_columns():
            return None
        lo = self._cell_addr(row, col)
        hi = min(lo + self._width, self._region.end)
        if lo < hi and self._is_highlighted(lo, hi):
            return DIFF_BACKGROUND
        return None

    def _hex_cell(self, row: int, col: int) -> str:
        addr = self._cell_addr(row, col)
        if addr >= self._region.end:
            return ""
        values = bytearray()
        for i in range(self._width):
            if addr + i >= self._region.end:
                return ""
            byte = self._byte(addr + i)
            if byte is None:
                return "?" * (self._width * 2)
            values.append(byte)
        value = int.from_bytes(values, "little")
        return f"{value:0{self._width * 2}X}"

    def _ascii_for_row(self, row: int) -> str:
        base = self._region.start + row * BYTES_PER_ROW
        chars = []
        for i in range(BYTES_PER_ROW):
            addr = base + i
            if addr >= self._region.end:
                break
            byte = self._byte(addr)
            if byte is None:
                chars.append(" ")
            elif 0x20 <= byte < 0x7F:
                chars.append(chr(byte))
            else:
                chars.append(".")
        return "".join(chars)

    def _byte(self, addr: int) -> int | None:
        byte = self._cache.get_byte(addr)
        if byte is None:
            base = self._cache.request_page_base(addr)
            if base is not None and self._request is not None:
                self._request(base, self._cache.page_size)
        return byte

    # --- editing ---------------------------------------------------------------

    def region_kind(self, addr: int) -> RegionKind:
        for region in self._memory_map:
            if region.contains(addr):
                return region.kind
        return RegionKind.UNKNOWN

    def flags(self, index: QModelIndex):
        base = super().flags(index)
        if not index.isValid() or self._region is None:
            return base
        if index.column() == self._hex_columns():  # ASCII column is read-only in v1
            return base
        addr = self._cell_addr(index.row(), index.column())
        if addr >= self._region.end:
            return base
        if self._editable_override:
            return base | Qt.ItemFlag.ItemIsEditable
        # Only RAM and flash are editable (flash via a backend read-modify-write). ROM, device and
        # unclassified regions are read-only.
        if self.region_kind(addr) not in (RegionKind.RAM, RegionKind.FLASH):
            return base
        return base | Qt.ItemFlag.ItemIsEditable

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or self._region is None or self._write is None:
            return False
        try:
            parsed = int(str(value), 16)
        except ValueError:
            return False
        if parsed < 0 or parsed >= (1 << (self._width * 8)):
            return False
        data = parsed.to_bytes(self._width, "little")
        addr = self._cell_addr(index.row(), index.column())
        self._write(addr, data)
        return True
