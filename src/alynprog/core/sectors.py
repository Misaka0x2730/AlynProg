"""Flash sector enumeration and coalescing.

A flash :class:`MemoryRegion` reports its erase-block size (``blocksize``) from the target's memory
map (GDB ``info mem`` → ``blocksize 0x400``). From that we enumerate erasable sectors, and when the
user picks several we coalesce adjacent ones into the fewest contiguous ``(addr, length)`` ranges so
the backend issues as few erase commands as possible.
"""

from __future__ import annotations

from dataclasses import dataclass

from alynprog.core.backend import MemoryRegion, RegionKind


@dataclass(frozen=True)
class Sector:
    index: int  # index within its region
    addr: int
    size: int
    region_name: str

    @property
    def end(self) -> int:
        return self.addr + self.size


def enumerate_sectors(regions: list[MemoryRegion]) -> list[Sector]:
    """Return all erasable flash sectors across *regions* (only flash regions with a blocksize)."""
    sectors: list[Sector] = []
    for region in regions:
        if region.kind is not RegionKind.FLASH or region.blocksize <= 0:
            continue
        name = region.name or "Flash"
        offset = 0
        index = 0
        while offset < region.size:
            size = min(region.blocksize, region.size - offset)
            sectors.append(Sector(index, region.start + offset, size, name))
            offset += size
            index += 1
    return sectors


def coalesce_ranges(sectors: list[Sector]) -> list[tuple[int, int]]:
    """Merge adjacent sectors into contiguous ``(addr, length)`` ranges.

    Sectors need not be pre-sorted. Two sectors are merged when one ends exactly where the next
    begins.
    """
    ordered = sorted(sectors, key=lambda s: s.addr)
    ranges: list[tuple[int, int]] = []
    for sector in ordered:
        if ranges and ranges[-1][0] + ranges[-1][1] == sector.addr:
            start, length = ranges[-1]
            ranges[-1] = (start, length + sector.size)
        else:
            ranges.append((sector.addr, sector.size))
    return ranges
