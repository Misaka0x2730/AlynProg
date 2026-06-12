"""Unit tests for the paged hex table model (needs a QApplication via the qapp fixture)."""

from __future__ import annotations

from PySide6.QtCore import Qt

from alynprog.core.backend import MemoryRegion, RegionKind
from alynprog.ui.panels.hexview.model import DIFF_BACKGROUND, HexTableModel


def _ram(start=0x2000_0000, size=0x1000):
    return MemoryRegion(start, size, RegionKind.RAM, name="SRAM")


def _flash(start=0x0800_0000, size=0x1000):
    return MemoryRegion(start, size, RegionKind.FLASH, name="Flash", blocksize=0x400)


def test_row_and_column_count(qapp):
    model = HexTableModel()
    model.set_region(MemoryRegion(0x2000_0000, 256, RegionKind.RAM))
    assert model.rowCount() == 16  # 256 / 16 bytes-per-row
    assert model.columnCount() == 17  # 16 byte columns + ASCII


def test_miss_requests_page_then_fills(qapp):
    model = HexTableModel()
    requests: list[tuple[int, int]] = []
    model.set_request_callback(lambda base, size: requests.append((base, size)))
    model.set_region(_ram())

    placeholder = model.data(model.index(0, 0))
    assert placeholder == "??"  # 1-byte cell renders 2 chars
    assert requests, "a page fetch should have been requested on the miss"

    base, _size = requests[0]
    model.fill_page(base, bytes(range(256)))
    assert model.data(model.index(0, 0)) == "00"
    assert model.data(model.index(0, 1)) == "01"


def test_width_switch_renders_little_endian(qapp):
    model = HexTableModel()
    model.set_request_callback(lambda base, size: None)
    model.set_region(_ram())
    model.fill_page(0x2000_0000, bytes(range(256)))

    model.set_width(4)
    assert model.columnCount() == 5  # 4 word columns + ASCII
    # bytes 00 01 02 03 little-endian -> 0x03020100
    assert model.data(model.index(0, 0)) == "03020100"


def test_ascii_column(qapp):
    model = HexTableModel()
    model.set_request_callback(lambda base, size: None)
    model.set_region(_ram())
    payload = b"Hello\x00\x01" + bytes(9)
    model.fill_page(0x2000_0000, payload + bytes(256 - len(payload)))
    ascii_text = model.data(model.index(0, 16))
    assert ascii_text.startswith("Hello..")


def test_flash_and_ram_are_editable_by_default(qapp):
    model = HexTableModel()
    flash = MemoryRegion(0x0800_0000, 0x1000, RegionKind.FLASH, blocksize=0x400)
    model.set_memory_map([flash, _ram()])

    model.set_region(_ram())
    assert bool(model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsEditable)

    # Flash is editable; the backend performs a sector read-modify-write under the hood.
    model.set_region(flash)
    assert bool(model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsEditable)


def test_ascii_highlight_maps_to_selected_cell(qapp):
    model = HexTableModel()
    model.set_region(_ram())
    assert model.ascii_column() == 16  # width 1 -> ASCII is column 16

    model.set_ascii_highlight(3, 4, 5)
    assert model.ascii_highlight() == (3, 4, 5)

    # Switching width clears the highlight and moves the ASCII column.
    model.set_width(4)
    assert model.ascii_highlight()[0] == -1
    assert model.ascii_column() == 4


def test_only_ram_and_flash_are_editable(qapp):
    model = HexTableModel()
    ram, flash = _ram(), _flash()
    rom = MemoryRegion(0x0000_0000, 0x100, RegionKind.UNKNOWN, name="ROM")
    model.set_memory_map([ram, flash, rom])

    model.set_region(ram)
    assert bool(model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsEditable)  # RAM editable

    model.set_region(flash)
    assert bool(model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsEditable)  # flash editable (RMW)

    # ROM / device / unclassified is read-only now — no opt-in checkbox.
    model.set_region(rom)
    assert not bool(model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsEditable)


def test_editable_override_makes_unknown_region_editable(qapp):
    model = HexTableModel()
    rom = MemoryRegion(0x0000_0000, 0x100, RegionKind.UNKNOWN, name="ROM")
    model.set_memory_map([rom])
    model.set_region(rom)
    # Without an override an UNKNOWN region is read-only...
    assert not bool(model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsEditable)
    # ...but a file document overrides the device gating so every byte is editable.
    model.set_editable_override(True)
    assert bool(model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsEditable)


def test_highlight_ranges_paint_background(qapp):
    model = HexTableModel()
    model.set_request_callback(lambda base, size: None)
    region = _ram(size=0x100)
    model.set_region(region)

    assert model.data(model.index(0, 0), Qt.ItemDataRole.BackgroundRole) is None
    # Highlight the single byte at region start; its hex cell tints via BackgroundRole.
    model.set_highlight_ranges([(region.start, region.start + 1)])
    assert model.data(model.index(0, 0), Qt.ItemDataRole.BackgroundRole) == DIFF_BACKGROUND
    assert model.data(model.index(0, 1), Qt.ItemDataRole.BackgroundRole) is None
    # The ASCII column is glyph-tinted by the delegate, so it reports the char offsets instead.
    ascii_col = model.ascii_column()
    assert model.data(model.index(0, ascii_col), Qt.ItemDataRole.BackgroundRole) is None
    assert model.diff_char_ranges(0) == [(0, 1)]
    assert model.diff_char_ranges(1) == []


def test_highlight_multiple_ranges_via_bisect(qapp):
    model = HexTableModel()
    model.set_request_callback(lambda base, size: None)
    region = _ram(size=0x100)
    model.set_region(region)
    # Several disjoint ranges in row 0: bytes 1, 4-5, and 8.
    base = region.start
    model.set_highlight_ranges([(base + 1, base + 2), (base + 4, base + 6), (base + 8, base + 9)])
    bg = Qt.ItemDataRole.BackgroundRole
    tinted = [c for c in range(16) if model.data(model.index(0, c), bg) == DIFF_BACKGROUND]
    assert tinted == [1, 4, 5, 8]
    # The ASCII column reports the same offsets for per-glyph tinting.
    assert model.diff_char_ranges(0) == [(1, 2), (4, 6), (8, 9)]


def test_highlight_ranges_are_merged(qapp):
    model = HexTableModel()
    model.set_request_callback(lambda base, size: None)
    region = _ram(size=0x100)
    model.set_region(region)
    base = region.start
    # Overlapping/adjacent ranges collapse into one contiguous highlight.
    model.set_highlight_ranges([(base + 2, base + 4), (base + 3, base + 6), (base + 6, base + 7)])
    assert model.diff_char_ranges(0) == [(2, 7)]


def test_highlight_persists_across_region_change(qapp):
    model = HexTableModel()
    model.set_request_callback(lambda base, size: None)
    region = _ram(size=0x100)
    model.set_region(region)
    model.set_highlight_ranges([(region.start, region.start + 4)])
    # Absolute highlights survive a region reselect (so diff navigation can cross segments)...
    model.set_region(region)
    assert model.data(model.index(0, 0), Qt.ItemDataRole.BackgroundRole) == DIFF_BACKGROUND
    # ...and are cleared only when the caller asks.
    model.set_highlight_ranges([])
    assert model.data(model.index(0, 0), Qt.ItemDataRole.BackgroundRole) is None


def test_setdata_calls_write_callback(qapp):
    model = HexTableModel()
    writes: list[tuple[int, bytes]] = []
    model.set_write_callback(lambda addr, data: writes.append((addr, data)))
    model.set_memory_map([_ram()])
    model.set_region(_ram())

    assert model.setData(model.index(0, 0), "AB", Qt.ItemDataRole.EditRole)
    assert writes == [(0x2000_0000, b"\xab")]
    # out-of-range / invalid input is rejected.
    assert not model.setData(model.index(0, 0), "ZZ", Qt.ItemDataRole.EditRole)
