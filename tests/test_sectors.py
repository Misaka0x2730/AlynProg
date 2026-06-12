"""Unit tests for flash sector enumeration and range coalescing."""

from __future__ import annotations

from alynprog.core.backend import MemoryRegion, RegionKind
from alynprog.core.sectors import coalesce_ranges, enumerate_sectors


def _flash(start=0x0800_0000, size=0x8000, blocksize=0x400):
    return MemoryRegion(start, size, RegionKind.FLASH, name="Flash", blocksize=blocksize)


def test_enumerate_uniform_sectors():
    sectors = enumerate_sectors([_flash()])
    assert len(sectors) == 32  # 0x8000 / 0x400
    assert sectors[0].addr == 0x0800_0000
    assert sectors[0].size == 0x400
    assert sectors[0].index == 0
    assert sectors[-1].addr == 0x0800_0000 + 31 * 0x400


def test_skips_ram_and_unknown_blocksize():
    regions = [
        MemoryRegion(0x2000_0000, 0x1000, RegionKind.RAM, blocksize=0),
        MemoryRegion(0x0800_0000, 0x1000, RegionKind.FLASH, blocksize=0),  # no blocksize
    ]
    assert enumerate_sectors(regions) == []


def test_partial_last_sector():
    sectors = enumerate_sectors([_flash(size=0x900, blocksize=0x400)])
    assert len(sectors) == 3
    assert sectors[2].size == 0x100  # remainder


def test_coalesce_all_contiguous_to_one_range():
    sectors = enumerate_sectors([_flash(size=0x4000, blocksize=0x1000)])
    assert coalesce_ranges(sectors) == [(0x0800_0000, 0x4000)]


def test_coalesce_with_gap():
    sectors = enumerate_sectors([_flash(size=0x4000, blocksize=0x1000)])
    chosen = [sectors[0], sectors[2], sectors[3]]  # gap at sector 1
    assert coalesce_ranges(chosen) == [(0x0800_0000, 0x1000), (0x0800_2000, 0x2000)]


def test_coalesce_is_order_independent():
    sectors = enumerate_sectors([_flash(size=0x3000, blocksize=0x1000)])
    shuffled = [sectors[2], sectors[0], sectors[1]]
    assert coalesce_ranges(shuffled) == [(0x0800_0000, 0x3000)]


def test_two_flash_regions_keep_separate_ranges():
    regions = [
        _flash(start=0x0800_0000, size=0x2000, blocksize=0x1000),
        _flash(start=0x0801_0000, size=0x2000, blocksize=0x1000),
    ]
    sectors = enumerate_sectors(regions)
    assert len(sectors) == 4
    assert coalesce_ranges(sectors) == [(0x0800_0000, 0x2000), (0x0801_0000, 0x2000)]
