"""Comparing a firmware buffer against device memory.

The diff engine is pure (no Qt, no I/O): :func:`diff_ranges` turns two equal-region byte strings
into the contiguous ranges where they differ, coalescing adjacent mismatched bytes and bounding the
result with a cap so a pathological alternating pattern cannot produce a runaway list. The session
worker reads device memory segment by segment and assembles a :class:`MemoryCompareResult` the UI
renders as a summary plus per-byte highlight ranges.
"""

from __future__ import annotations

from dataclasses import dataclass

# Upper bound on differing ranges kept per segment, a memory backstop against a pathological
# every-other-byte diff. High enough that real firmware compares are never truncated.
DEFAULT_DIFF_CAP = 1_000_000


@dataclass(frozen=True)
class DiffRange:
    addr: int
    length: int

    @property
    def end(self) -> int:
        return self.addr + self.length


@dataclass(frozen=True)
class SegmentCompare:
    addr: int
    expected: bytes  # the file bytes
    actual: bytes  # the device bytes read back
    diffs: tuple[DiffRange, ...]
    truncated: bool = False

    @property
    def length(self) -> int:
        return len(self.expected)

    @property
    def matched(self) -> bool:
        return not self.diffs and not self.truncated

    @property
    def bytes_differing(self) -> int:
        return sum(d.length for d in self.diffs)


@dataclass(frozen=True)
class MemoryCompareResult:
    segments: tuple[SegmentCompare, ...]

    @property
    def matched(self) -> bool:
        return all(seg.matched for seg in self.segments)

    @property
    def truncated(self) -> bool:
        return any(seg.truncated for seg in self.segments)

    @property
    def bytes_compared(self) -> int:
        return sum(seg.length for seg in self.segments)

    @property
    def bytes_differing(self) -> int:
        return sum(seg.bytes_differing for seg in self.segments)

    @property
    def range_count(self) -> int:
        return sum(len(seg.diffs) for seg in self.segments)

    @property
    def all_diffs(self) -> list[DiffRange]:
        """Every differing range across all segments, in ascending address order."""
        return sorted((d for seg in self.segments for d in seg.diffs), key=lambda d: d.addr)

    @property
    def first_mismatch(self) -> int | None:
        diffs = self.all_diffs
        return diffs[0].addr if diffs else None


def diff_ranges(
    expected: bytes, actual: bytes, base: int = 0, cap: int = DEFAULT_DIFF_CAP
) -> tuple[list[DiffRange], bool]:
    """Return the ranges where *expected* and *actual* differ, plus whether the cap truncated them.

    Addresses are reported relative to *base*. Bytes are compared over the shorter of the two; if
    the lengths differ, the unmatched tail counts as one differing range. At most *cap* ranges are
    returned; on overflow the list stops and ``truncated`` is ``True``.
    """
    ranges: list[DiffRange] = []
    common = min(len(expected), len(actual))
    index = 0
    while index < common:
        if expected[index] == actual[index]:
            index += 1
            continue
        start = index
        while index < common and expected[index] != actual[index]:
            index += 1
        if len(ranges) >= cap:
            return ranges, True
        ranges.append(DiffRange(base + start, index - start))

    if len(expected) != len(actual):
        tail = abs(len(expected) - len(actual))
        if len(ranges) >= cap:
            return ranges, True
        ranges.append(DiffRange(base + common, tail))
    return ranges, False


def compare_segment(
    expected: bytes, actual: bytes, base: int, cap: int = DEFAULT_DIFF_CAP
) -> SegmentCompare:
    ranges, truncated = diff_ranges(expected, actual, base, cap)
    return SegmentCompare(base, bytes(expected), bytes(actual), tuple(ranges), truncated)


def compare_buffers(
    left: list[tuple[int, bytes]],
    right: list[tuple[int, bytes]],
    cap: int = DEFAULT_DIFF_CAP,
) -> MemoryCompareResult:
    """Compare two address-tagged buffers over the address ranges they have in common.

    Each maximal window where a *left* and a *right* segment overlap in address becomes one
    :class:`SegmentCompare` — the left bytes as ``actual``, the right bytes as ``expected`` — so the
    result drops straight into the same side-by-side view the device compare uses. Bytes that exist
    on only one side are not compared (mirroring how the device compare clamps to the device map);
    two buffers that share no address range yield an empty result.
    """
    segments: list[SegmentCompare] = []
    for left_addr, left_data in left:
        for right_addr, right_data in right:
            lo = max(left_addr, right_addr)
            hi = min(left_addr + len(left_data), right_addr + len(right_data))
            if lo < hi:
                actual = left_data[lo - left_addr : hi - left_addr]
                expected = right_data[lo - right_addr : hi - right_addr]
                segments.append(compare_segment(expected, actual, lo, cap))
    segments.sort(key=lambda seg: seg.addr)
    return MemoryCompareResult(tuple(segments))
