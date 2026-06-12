"""Unit tests for the minimal ELF program-header reader."""

from __future__ import annotations

import struct

import pytest

from alynprog.core.elf import load_elf_segments
from alynprog.core.errors import ImageError


def _build_elf(segments, *, bits=32, endian="<"):
    """Build a tiny but valid ELF carrying the given ``(p_paddr, p_vaddr, data)`` PT_LOAD entries.

    A ``data`` of ``b""`` produces a ``.bss``-style segment (``p_filesz == 0``) with no file bytes.
    """
    is_64 = bits == 64
    ehsize = 64 if is_64 else 52
    phentsize = 56 if is_64 else 32
    e_phoff = ehsize
    phnum = len(segments)
    data_start = e_phoff + phnum * phentsize

    offsets = []
    cur = data_start
    for _paddr, _vaddr, data in segments:
        offsets.append(cur)
        cur += len(data)

    ehdr = bytearray(ehsize)
    ehdr[0:4] = b"\x7fELF"
    ehdr[4] = 2 if is_64 else 1
    ehdr[5] = 1 if endian == "<" else 2
    ehdr[6] = 1  # EI_VERSION
    if is_64:
        struct.pack_into(
            endian + "HHIQQQIHHHHHH",
            ehdr,
            16,
            2,
            40,
            1,
            0,
            e_phoff,
            0,
            0,
            ehsize,
            phentsize,
            phnum,
            0,
            0,
            0,
        )
    else:
        struct.pack_into(
            endian + "HHIIIIIHHHHHH",
            ehdr,
            16,
            2,
            40,
            1,
            0,
            e_phoff,
            0,
            0,
            ehsize,
            phentsize,
            phnum,
            0,
            0,
            0,
        )

    phdrs = bytearray()
    for (paddr, vaddr, data), off in zip(segments, offsets, strict=True):
        if is_64:
            phdrs += struct.pack(
                endian + "IIQQQQQQ", 1, 7, off, vaddr, paddr, len(data), len(data), 8
            )
        else:
            phdrs += struct.pack(
                endian + "IIIIIIII", 1, off, vaddr, paddr, len(data), len(data), 7, 4
            )

    out = bytearray(ehdr) + phdrs
    for _paddr, _vaddr, data in segments:
        out += data
    return bytes(out)


def test_reads_single_segment(tmp_path):
    p = tmp_path / "fw.elf"
    p.write_bytes(_build_elf([(0x0800_0000, 0x0800_0000, b"\xde\xad\xbe\xef")]))
    segments = load_elf_segments(p)
    assert len(segments) == 1
    assert segments[0].addr == 0x0800_0000
    assert segments[0].data == b"\xde\xad\xbe\xef"


def test_prefers_load_address_over_runtime_address(tmp_path):
    # A .data-style segment: stored in flash (LMA), runs from RAM (VMA). We must keep the LMA.
    p = tmp_path / "fw.elf"
    p.write_bytes(_build_elf([(0x0800_4000, 0x2000_0000, b"\x01\x02\x03\x04")]))
    segments = load_elf_segments(p)
    assert segments[0].addr == 0x0800_4000


def test_falls_back_to_vaddr_when_paddr_is_zero(tmp_path):
    p = tmp_path / "fw.elf"
    p.write_bytes(_build_elf([(0, 0x2000_0000, b"\xaa\xbb")]))
    assert load_elf_segments(p)[0].addr == 0x2000_0000


def test_skips_zero_length_bss_segment(tmp_path):
    p = tmp_path / "fw.elf"
    p.write_bytes(
        _build_elf(
            [
                (0x0800_0000, 0x0800_0000, b"\x11\x22\x33\x44"),
                (0x2000_0000, 0x2000_0000, b""),  # .bss, no file bytes
            ]
        )
    )
    segments = load_elf_segments(p)
    assert len(segments) == 1
    assert segments[0].addr == 0x0800_0000


def test_segments_sorted_by_address(tmp_path):
    p = tmp_path / "fw.elf"
    p.write_bytes(
        _build_elf(
            [
                (0x0800_4000, 0x0800_4000, b"\x02"),
                (0x0800_0000, 0x0800_0000, b"\x01"),
            ]
        )
    )
    addrs = [seg.addr for seg in load_elf_segments(p)]
    assert addrs == [0x0800_0000, 0x0800_4000]


def test_reads_64bit_big_endian(tmp_path):
    p = tmp_path / "fw.elf"
    p.write_bytes(_build_elf([(0x4000_0000, 0x4000_0000, b"\xca\xfe")], bits=64, endian=">"))
    segments = load_elf_segments(p)
    assert segments[0].addr == 0x4000_0000
    assert segments[0].data == b"\xca\xfe"


def test_not_an_elf_raises(tmp_path):
    p = tmp_path / "not.elf"
    p.write_bytes(b"hello, world, not an elf at all")
    with pytest.raises(ImageError):
        load_elf_segments(p)


def test_no_loadable_segments_raises(tmp_path):
    p = tmp_path / "fw.elf"
    p.write_bytes(_build_elf([(0x2000_0000, 0x2000_0000, b"")]))  # only a .bss-style segment
    with pytest.raises(ImageError):
        load_elf_segments(p)
