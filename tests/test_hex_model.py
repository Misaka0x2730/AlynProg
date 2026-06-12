"""Unit tests for the paged hex table model (needs a QApplication via the qapp fixture)."""

from __future__ import annotations

from PySide6.QtCore import Qt

from alynprog.core.backend import MemoryRegion, RegionKind
from alynprog.ui.panels.hexview.model import HexTableModel


def _ram(start=0x2000_0000, size=0x1000):
    return MemoryRegion(start, size, RegionKind.RAM, name="SRAM")


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


def test_unknown_region_editable_only_after_optin(qapp):
    model = HexTableModel()
    model.set_memory_map([])  # nothing classified -> unknown
    model.set_region(MemoryRegion(0x3000_0000, 0x100, RegionKind.UNKNOWN))
    assert not bool(model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsEditable)

    model.set_allow_unknown_edit(True)
    assert bool(model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsEditable)


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
