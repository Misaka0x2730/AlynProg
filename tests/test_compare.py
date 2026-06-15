"""Unit tests for the firmware-vs-device diff engine."""

from __future__ import annotations

from alynprog.core.compare import (
    DiffRange,
    MemoryCompareResult,
    SegmentCompare,
    compare_buffers,
    compare_segment,
    diff_ranges,
)


def test_identical_buffers_have_no_diffs():
    ranges, truncated = diff_ranges(b"\x01\x02\x03", b"\x01\x02\x03", base=0x0800_0000)
    assert ranges == []
    assert truncated is False


def test_single_differing_byte():
    ranges, truncated = diff_ranges(b"\x01\x02\x03", b"\x01\xff\x03", base=0x0800_0000)
    assert ranges == [DiffRange(0x0800_0001, 1)]
    assert truncated is False


def test_adjacent_mismatches_coalesce():
    ranges, _ = diff_ranges(b"\xaa\xaa\xaa\xaa", b"\xaa\x00\x00\xaa", base=0x1000)
    assert ranges == [DiffRange(0x1001, 2)]


def test_separate_runs_stay_separate():
    ranges, _ = diff_ranges(b"\x01\x02\x03\x04\x05", b"\xff\x02\xff\x04\xff", base=0)
    assert ranges == [DiffRange(0, 1), DiffRange(2, 1), DiffRange(4, 1)]


def test_length_mismatch_tail_counts_as_diff():
    ranges, _ = diff_ranges(b"\x01\x02\x03\x04", b"\x01\x02", base=0x2000_0000)
    assert ranges == [DiffRange(0x2000_0002, 2)]


def test_cap_truncates_runaway_diffs():
    # Every odd byte differs, producing many separate 1-byte ranges.
    expected = b"\x00\x01" * 32
    actual = b"\x00\xff" * 32
    ranges, truncated = diff_ranges(expected, actual, base=0, cap=3)
    assert len(ranges) == 3
    assert truncated is True


def test_segment_compare_matched():
    seg = compare_segment(b"\x01\x02", b"\x01\x02", base=0x0800_0000)
    assert seg.matched is True
    assert seg.bytes_differing == 0
    # The segment carries both sides' bytes for the side-by-side view.
    assert seg.expected == b"\x01\x02"
    assert seg.actual == b"\x01\x02"
    assert seg.length == 2


def test_segment_compare_with_diffs():
    seg = compare_segment(b"\x01\x02\x03", b"\x01\xff\xff", base=0x0800_0000)
    assert seg.matched is False
    assert seg.diffs == (DiffRange(0x0800_0001, 2),)
    assert seg.bytes_differing == 2
    assert seg.expected == b"\x01\x02\x03"
    assert seg.actual == b"\x01\xff\xff"


def _seg(addr, length, diffs):
    # Build a SegmentCompare with placeholder bytes of the right length for aggregate tests.
    return SegmentCompare(addr, bytes(length), bytes(length), diffs)


def test_result_aggregates_segments():
    result = MemoryCompareResult(
        segments=(
            _seg(0x0800_0000, 4, (DiffRange(0x0800_0002, 1),)),
            _seg(0x2000_0000, 8, ()),
        )
    )
    assert result.matched is False
    assert result.bytes_compared == 12
    assert result.bytes_differing == 1
    assert result.range_count == 1
    assert result.first_mismatch == 0x0800_0002


def test_result_all_match():
    result = MemoryCompareResult(
        segments=(
            _seg(0x0800_0000, 4, ()),
            _seg(0x2000_0000, 8, ()),
        )
    )
    assert result.matched is True
    assert result.first_mismatch is None


# --- compare_buffers (file-vs-file) -------------------------------------------


def test_compare_buffers_diffs_overlapping_region():
    left = [(0x0800_0000, b"\x01\x02\x03\x04")]
    right = [(0x0800_0000, b"\x01\xff\x03\xff")]
    result = compare_buffers(left, right)
    assert result.bytes_compared == 4
    assert result.range_count == 2  # offsets 1 and 3 differ
    assert result.matched is False
    # Left bytes land in `actual`, right bytes in `expected` (the side-by-side view's convention).
    assert result.segments[0].actual == b"\x01\x02\x03\x04"
    assert result.segments[0].expected == b"\x01\xff\x03\xff"


def test_compare_buffers_only_compares_the_overlap():
    # Left runs 0x100..0x108, right 0x104..0x10C -> only the 4-byte overlap 0x104..0x108 compares.
    left = [(0x100, bytes(8))]
    right = [(0x104, b"\xff\xff\xff\xff\xff\xff\xff\xff")]
    result = compare_buffers(left, right)
    assert result.bytes_compared == 4
    assert result.segments[0].addr == 0x104
    assert result.bytes_differing == 4


def test_compare_buffers_no_overlap_is_empty():
    left = [(0x0000_0000, b"\x00\x00")]  # e.g. a .bin opened at offset 0
    right = [(0x0800_0000, b"\x00\x00")]  # vs a .hex at flash
    assert compare_buffers(left, right).segments == ()


def test_compare_buffers_matches_identical_files():
    left = [(0x0, b"firmware")]
    right = [(0x0, b"firmware")]
    result = compare_buffers(left, right)
    assert result.matched is True
    assert result.bytes_compared == 8


def test_compare_buffers_multiple_segments_sorted():
    left = [(0x2000, b"\xaa\xaa"), (0x1000, b"\xbb\xbb")]
    right = [(0x1000, b"\xbb\x00"), (0x2000, b"\xaa\xaa")]
    result = compare_buffers(left, right)
    # One window per overlapping segment pair, emitted in ascending address order.
    assert [seg.addr for seg in result.segments] == [0x1000, 0x2000]
    assert result.bytes_differing == 1  # only the second byte of the 0x1000 segment
