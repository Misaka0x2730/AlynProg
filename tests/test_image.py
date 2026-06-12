"""Unit tests for firmware image loading and BIN→HEX conversion."""

from __future__ import annotations

import struct

import pytest
from intelhex import IntelHex

from alynprog.core.errors import ImageError
from alynprog.core.image import FirmwareImage, ImageKind


def _write_elf_stub(path):
    # Just the magic is enough for kind detection; we never parse ELF contents in v1.
    path.write_bytes(b"\x7fELF" + b"\x00" * 60)


def test_detects_elf_by_magic(tmp_path):
    p = tmp_path / "firmware.bin.unknownext"
    _write_elf_stub(p)
    img = FirmwareImage.load(p)
    assert img.kind is ImageKind.ELF
    assert img.segments == []
    assert img.address_range() is None


def test_detects_elf_by_extension(tmp_path):
    p = tmp_path / "firmware.elf"
    _write_elf_stub(p)
    assert FirmwareImage.load(p).kind is ImageKind.ELF


def test_loads_hex_segments(tmp_path):
    ih = IntelHex()
    ih.puts(0x0800_0000, b"\xde\xad\xbe\xef")
    p = tmp_path / "fw.hex"
    ih.write_hex_file(str(p))

    img = FirmwareImage.load(p)
    assert img.kind is ImageKind.HEX
    assert img.total_size == 4
    assert img.address_range() == (0x0800_0000, 0x0800_0004)
    assert img.segments[0].data == b"\xde\xad\xbe\xef"


def test_bin_requires_base_address(tmp_path):
    p = tmp_path / "fw.bin"
    p.write_bytes(b"\x01\x02\x03\x04")
    with pytest.raises(ImageError):
        FirmwareImage.load(p)


def test_bin_converts_to_hex(tmp_path):
    p = tmp_path / "fw.bin"
    payload = struct.pack("<4I", 1, 2, 3, 4)
    p.write_bytes(payload)
    img = FirmwareImage.load(p, base_addr=0x0800_0000)
    assert img.kind is ImageKind.BIN
    assert img.total_size == len(payload)

    out = img.loadable_path(tmp_path)
    assert out.suffix == ".hex"
    reloaded = IntelHex(str(out))
    assert reloaded.tobinstr(start=0x0800_0000, end=0x0800_0000 + len(payload) - 1) == payload


def test_loadable_path_passes_through_elf_and_hex(tmp_path):
    elf = tmp_path / "fw.elf"
    _write_elf_stub(elf)
    assert FirmwareImage.load(elf).loadable_path(tmp_path) == elf


def test_missing_file_raises(tmp_path):
    with pytest.raises(ImageError):
        FirmwareImage.load(tmp_path / "nope.elf")
