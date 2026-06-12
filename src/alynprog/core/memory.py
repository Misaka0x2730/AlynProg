"""A paged, LRU memory read-cache shared by the hex view and the session worker.

The hex model asks the cache for individual bytes. On a miss it asks the cache for the page base
address to fetch (deduplicated against in-flight requests), hands that to the worker, and later
fills the returned page. This keeps the UI responsive over large, randomly-accessed regions while
issuing at most one outstanding read per page.
"""

from __future__ import annotations

from collections import OrderedDict

DEFAULT_PAGE_SIZE = 256
DEFAULT_CAPACITY_PAGES = 1024  # ~256 KiB cached at the default page size


class PagedMemoryCache:
    def __init__(
        self,
        page_size: int = DEFAULT_PAGE_SIZE,
        capacity_pages: int = DEFAULT_CAPACITY_PAGES,
    ) -> None:
        if page_size <= 0:
            raise ValueError("page_size must be positive")
        self._page_size = page_size
        self._capacity = max(1, capacity_pages)
        self._pages: OrderedDict[int, bytes] = OrderedDict()
        self._inflight: set[int] = set()

    @property
    def page_size(self) -> int:
        return self._page_size

    def page_index(self, addr: int) -> int:
        return addr // self._page_size

    def page_base(self, addr: int) -> int:
        return self.page_index(addr) * self._page_size

    # --- reads -----------------------------------------------------------------

    def get_byte(self, addr: int) -> int | None:
        """Return the cached byte at *addr*, or ``None`` on a miss."""
        index = self.page_index(addr)
        page = self._pages.get(index)
        if page is None:
            return None
        self._pages.move_to_end(index)
        offset = addr - index * self._page_size
        if 0 <= offset < len(page):
            return page[offset]
        return None

    def request_page_base(self, addr: int) -> int | None:
        """If *addr*'s page is neither cached nor in flight, mark it in-flight and return the page
        base address the worker should read (``page_size`` bytes). Otherwise return ``None``."""
        index = self.page_index(addr)
        if index in self._pages or index in self._inflight:
            return None
        self._inflight.add(index)
        return index * self._page_size

    def is_inflight(self, addr: int) -> bool:
        return self.page_index(addr) in self._inflight

    # --- fills / invalidation --------------------------------------------------

    def fill(self, page_base: int, data: bytes) -> None:
        """Store a fetched page (its base must be page-aligned) and clear its in-flight mark."""
        index = page_base // self._page_size
        self._inflight.discard(index)
        self._pages[index] = bytes(data)
        self._pages.move_to_end(index)
        while len(self._pages) > self._capacity:
            self._pages.popitem(last=False)

    def invalidate(self, addr: int | None = None, length: int | None = None) -> None:
        """Drop cached pages. With no arguments, clears everything; otherwise clears the pages
        overlapping ``[addr, addr+length)``."""
        if addr is None or length is None:
            self._pages.clear()
            self._inflight.clear()
            return
        first = self.page_index(addr)
        last = self.page_index(addr + max(0, length - 1))
        for index in range(first, last + 1):
            self._pages.pop(index, None)
            self._inflight.discard(index)

    def __len__(self) -> int:
        return len(self._pages)
