"""Unit tests for the editable firmware document (open / read / write / save)."""

from __future__ import annotations

import struct

import pytest
from intelhex import IntelHex

from alynprog.core.document import FirmwareDocument
from alynprog.core.errors import ImageError
from alynprog.core.image import ImageKind
from tests.test_elf import _build_elf


def _write_bin(path, data):
    path.write_bytes(data)
    return path


def _write_hex(path, segments):
    ih = IntelHex()
    for addr, data in segments:
        ih.puts(addr, data)
    ih.write_hex_file(str(path))
    return path


# --- opening ------------------------------------------------------------------


def test_open_bin_starts_at_offset_zero(tmp_path):
    p = _write_bin(tmp_path / "fw.bin", b"\x01\x02\x03\x04")
    doc = FirmwareDocument.open(p)
    assert doc.kind is ImageKind.BIN
    regions = doc.regions()
    assert len(regions) == 1
    assert regions[0].start == 0
    assert regions[0].size == 4
    assert regions[0].name == "fw.bin"
    assert doc.read(0, 4) == b"\x01\x02\x03\x04"


def test_open_hex_exposes_one_region_per_segment(tmp_path):
    p = _write_hex(
        tmp_path / "fw.hex",
        [(0x0800_0000, b"\xaa\xbb"), (0x0800_1000, b"\xcc\xdd\xee")],
    )
    doc = FirmwareDocument.open(p)
    assert doc.kind is ImageKind.HEX
    regions = doc.regions()
    assert [(r.start, r.size) for r in regions] == [(0x0800_0000, 2), (0x0800_1000, 3)]
    assert regions[0].name == "Segment 0"


def test_open_elf(tmp_path):
    p = tmp_path / "fw.elf"
    p.write_bytes(_build_elf([(0x0800_0000, 0x0800_0000, b"\x11\x22\x33\x44")]))
    doc = FirmwareDocument.open(p)
    assert doc.kind is ImageKind.ELF
    assert doc.read(0x0800_0000, 4) == b"\x11\x22\x33\x44"
    assert doc.supports_in_place_save is False


def test_open_missing_file_raises(tmp_path):
    with pytest.raises(ImageError):
        FirmwareDocument.open(tmp_path / "nope.bin")


# --- reading (paging) ---------------------------------------------------------


def test_read_pads_past_segment_end(tmp_path):
    p = _write_bin(tmp_path / "fw.bin", b"\x01\x02")
    doc = FirmwareDocument.open(p)
    # A page request straddling the end of the 2-byte segment pads the rest (never displayed).
    assert doc.read(0, 4) == b"\x01\x02\xff\xff"


def test_read_positions_data_within_unaligned_page(tmp_path):
    p = _write_hex(tmp_path / "fw.hex", [(0x0800_0004, b"\xab\xcd")])
    doc = FirmwareDocument.open(p)
    page = doc.read(0x0800_0000, 8)
    assert page[4:6] == b"\xab\xcd"
    assert page[0:4] == b"\xff\xff\xff\xff"


# --- writing / dirty ----------------------------------------------------------


def test_write_marks_dirty_and_updates_bytes(tmp_path):
    p = _write_bin(tmp_path / "fw.bin", b"\x01\x02\x03\x04")
    doc = FirmwareDocument.open(p)
    assert doc.dirty is False
    assert doc.write(0x0000_0002, b"\xff") is True
    assert doc.dirty is True
    assert doc.read(0, 4) == b"\x01\x02\xff\x04"


def test_write_outside_segment_is_rejected(tmp_path):
    p = _write_bin(tmp_path / "fw.bin", b"\x01\x02")
    doc = FirmwareDocument.open(p)
    assert doc.write(0x10, b"\xff") is False
    assert doc.dirty is False


def test_write_spanning_segment_end_is_rejected(tmp_path):
    p = _write_bin(tmp_path / "fw.bin", b"\x01\x02")
    doc = FirmwareDocument.open(p)
    assert doc.write(0x1, b"\xaa\xbb") is False  # would run one byte past the end


# --- saving -------------------------------------------------------------------


def test_save_bin_in_place(tmp_path):
    p = _write_bin(tmp_path / "fw.bin", b"\x01\x02\x03\x04")
    doc = FirmwareDocument.open(p)
    doc.write(0, b"\xde")
    doc.save()
    assert doc.dirty is False
    assert p.read_bytes() == b"\xde\x02\x03\x04"


def test_save_hex_in_place_round_trips(tmp_path):
    p = _write_hex(tmp_path / "fw.hex", [(0x0800_0000, b"\x00\x01\x02\x03")])
    doc = FirmwareDocument.open(p)
    doc.write(0x0800_0001, b"\xaa")
    doc.save()
    reloaded = IntelHex(str(p))
    assert reloaded.tobinstr(start=0x0800_0000, end=0x0800_0003) == b"\x00\xaa\x02\x03"


def test_elf_save_in_place_raises(tmp_path):
    p = tmp_path / "fw.elf"
    p.write_bytes(_build_elf([(0x0800_0000, 0x0800_0000, b"\x11\x22")]))
    doc = FirmwareDocument.open(p)
    with pytest.raises(ImageError):
        doc.save()


def test_save_as_elf_to_hex_changes_document(tmp_path):
    src = tmp_path / "fw.elf"
    src.write_bytes(_build_elf([(0x0800_0000, 0x0800_0000, b"\x11\x22\x33\x44")]))
    doc = FirmwareDocument.open(src)
    out = tmp_path / "exported.hex"
    doc.save_as(out)
    assert doc.kind is ImageKind.HEX
    assert doc.path == out
    assert doc.supports_in_place_save is True
    reloaded = IntelHex(str(out))
    assert reloaded.tobinstr(start=0x0800_0000, end=0x0800_0003) == b"\x11\x22\x33\x44"


def test_save_as_bin_refuses_multiple_segments(tmp_path):
    p = _write_hex(
        tmp_path / "fw.hex",
        [(0x0800_0000, b"\xaa"), (0x0800_1000, b"\xbb")],
    )
    doc = FirmwareDocument.open(p)
    with pytest.raises(ImageError):
        doc.save_as(tmp_path / "out.bin")


def test_segment_views_snapshot(tmp_path):
    payload = struct.pack("<2I", 0xDEAD_BEEF, 0x1234_5678)
    p = _write_bin(tmp_path / "fw.bin", payload)
    doc = FirmwareDocument.open(p)
    views = doc.segment_views()
    assert views == [(0, payload)]
