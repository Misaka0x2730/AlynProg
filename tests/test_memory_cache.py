"""Unit tests for the paged LRU memory cache."""

from __future__ import annotations

from alynprog.core.memory import PagedMemoryCache


def test_miss_then_request_then_fill():
    cache = PagedMemoryCache(page_size=16, capacity_pages=4)
    addr = 0x2000_0000
    assert cache.get_byte(addr) is None

    base = cache.request_page_base(addr)
    assert base == addr  # page-aligned base
    # A second request for the same page is deduplicated while in flight.
    assert cache.request_page_base(addr + 1) is None
    assert cache.is_inflight(addr)

    cache.fill(base, bytes(range(16)))
    assert cache.get_byte(addr) == 0
    assert cache.get_byte(addr + 5) == 5
    assert not cache.is_inflight(addr)
    # Once cached, no fetch is requested.
    assert cache.request_page_base(addr) is None


def test_partial_page_marks_missing_bytes_but_does_not_refetch():
    cache = PagedMemoryCache(page_size=16)
    base = cache.request_page_base(0x100)
    cache.fill(base, b"\x01\x02")  # short read
    assert cache.get_byte(0x100) == 1
    assert cache.get_byte(0x105) is None  # missing tail -> placeholder
    assert cache.request_page_base(0x105) is None  # page is cached, no refetch


def test_lru_eviction():
    cache = PagedMemoryCache(page_size=16, capacity_pages=2)
    cache.fill(0x00, b"\x00" * 16)
    cache.fill(0x10, b"\x11" * 16)
    # Touch page 0 so it becomes most-recently-used.
    assert cache.get_byte(0x00) == 0
    cache.fill(0x20, b"\x22" * 16)  # evicts the least-recently-used (page 0x10)
    assert cache.get_byte(0x10) is None
    assert cache.get_byte(0x00) == 0
    assert cache.get_byte(0x20) == 0x22


def test_invalidate_range():
    cache = PagedMemoryCache(page_size=16, capacity_pages=8)
    for base in (0x00, 0x10, 0x20):
        cache.fill(base, bytes([base]) * 16)
    cache.invalidate(0x10, 16)
    assert cache.get_byte(0x10) is None
    assert cache.get_byte(0x00) == 0x00
    assert cache.get_byte(0x20) == 0x20


def test_invalidate_all():
    cache = PagedMemoryCache(page_size=16)
    cache.fill(0x00, b"\x07" * 16)
    cache.invalidate()
    assert len(cache) == 0
    assert cache.get_byte(0x00) is None
