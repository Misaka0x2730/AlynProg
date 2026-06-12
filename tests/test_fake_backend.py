"""Tests for the in-process simulated backend (also the ABC's acid test: no GDB involved)."""

from __future__ import annotations

import struct

import pytest

from alynprog.backends.fake.backend import FLASH_BASE, RAM_BASE, FakeBackend
from alynprog.core.backend import ConnectOptions, EraseKind, EraseSpec, ProbeBackend
from alynprog.core.errors import TargetError
from alynprog.core.image import FirmwareImage


def _connected() -> FakeBackend:
    backend = FakeBackend()
    backend.connect(backend.discover()[0], ConnectOptions())
    backend.attach(1)
    return backend


def test_fake_is_a_probe_backend_without_gdb():
    assert issubclass(FakeBackend, ProbeBackend)


def test_discover_and_memory_map():
    backend = _connected()
    regions = backend.memory_map()
    assert any(r.start == FLASH_BASE for r in regions)
    assert any(r.start == RAM_BASE for r in regions)


def test_flash_reads_as_erased_and_ram_as_zero():
    backend = _connected()
    assert backend.read_memory(FLASH_BASE, 4) == b"\xff\xff\xff\xff"
    assert backend.read_memory(RAM_BASE, 4) == b"\x00\x00\x00\x00"


def test_ram_write_round_trip():
    backend = _connected()
    backend.write_memory(RAM_BASE + 8, b"\xca\xfe")
    assert backend.read_memory(RAM_BASE + 8, 2) == b"\xca\xfe"


def test_flash_write_uses_read_modify_write():
    backend = _connected()
    # Pre-load a sector so we can confirm neighbours survive the RMW.
    bin_bytes = b"\xaa" * 0x100
    sector = FLASH_BASE
    backend._program_flash_block(sector, bin_bytes + b"\xff" * (0x4000 - len(bin_bytes)))
    assert backend.read_memory(sector + 0x10, 1) == b"\xaa"

    # Edit a single byte via the public write path -> read-modify-write the whole sector.
    backend.write_memory(sector + 0x10, b"\x5a")
    assert backend.read_memory(sector + 0x10, 1) == b"\x5a"
    # Neighbouring bytes in the same sector are preserved by the RMW.
    assert backend.read_memory(sector + 0x11, 1) == b"\xaa"
    assert backend.read_memory(sector + 0x0F, 1) == b"\xaa"


def test_raw_write_to_flash_still_rejected():
    # The low-level raw path must never poke flash directly.
    backend = _connected()
    with pytest.raises(TargetError):
        backend._write_raw_memory(FLASH_BASE, b"\x00")


def test_program_writes_flash_and_verifies(tmp_path):
    backend = _connected()
    payload = struct.pack("<4I", 0xDEAD, 0xBEEF, 1, 2)
    bin_path = tmp_path / "fw.bin"
    bin_path.write_bytes(payload)
    image = FirmwareImage.load(bin_path, base_addr=FLASH_BASE)

    seen: list[tuple[int, int]] = []
    result = backend.program(image, progress=lambda d, t: seen.append((d, t)), verify=True)
    assert result.verified is True
    assert seen[-1][0] == seen[-1][1]
    assert backend.read_memory(FLASH_BASE, len(payload)) == payload


def test_mass_erase_resets_flash(tmp_path):
    backend = _connected()
    bin_path = tmp_path / "fw.bin"
    bin_path.write_bytes(b"\x01\x02\x03\x04")
    backend.program(FirmwareImage.load(bin_path, base_addr=FLASH_BASE), verify=False)
    assert backend.read_memory(FLASH_BASE, 4) == b"\x01\x02\x03\x04"

    backend.erase(EraseSpec(kind=EraseKind.MASS))
    assert backend.read_memory(FLASH_BASE, 4) == b"\xff\xff\xff\xff"


def test_registers_available():
    backend = _connected()
    regs = backend.read_registers()
    assert "pc" in regs and "sp" in regs


def test_rmw_spanning_two_sectors():
    from alynprog.backends.fake.backend import FLASH_BLOCKSIZE

    backend = _connected()
    # Write across a sector boundary: last byte of sector 0 + first byte of sector 1.
    addr = FLASH_BASE + FLASH_BLOCKSIZE - 1
    backend.write_memory(addr, b"\x11\x22")
    assert backend.read_memory(addr, 2) == b"\x11\x22"
    # Bytes just outside the written range stay erased (0xFF).
    assert backend.read_memory(addr - 1, 1) == b"\xff"
    assert backend.read_memory(addr + 2, 1) == b"\xff"
