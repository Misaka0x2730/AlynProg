"""Data sources that feed a :class:`~alynprog.ui.panels.hexview.view.HexPane`.

The hex model pulls bytes through a page-request callback and pushes edits through a write
callback. A *source* adapts those callbacks to a concrete backing store. The device-memory pane
talks to the session directly; this module provides the file-document adapter.

The page-request callback fires from inside ``HexTableModel.data()`` while the table is painting,
so a source must never emit ``page_ready`` synchronously from it — doing so would re-enter the
model's ``dataChanged`` during a paint. :class:`FileDocumentSource` therefore defers the fill to
the next event-loop turn with a zero-delay timer, mirroring how the device source only ever
delivers pages from a later signal.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from alynprog.core.document import FirmwareDocument


class FileDocumentSource(QObject):
    """Serves a :class:`FirmwareDocument` to a hex pane: deferred page reads, synchronous writes."""

    page_ready = Signal(int, object)  # (base, bytes)
    write_done = Signal(int, int, bool, str)  # (addr, length, ok, error)
    changed = Signal()  # the document was edited (drives the tab's dirty marker)

    def __init__(self, document: FirmwareDocument, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._document = document

    @property
    def document(self) -> FirmwareDocument:
        return self._document

    def request_page(self, base: int, size: int) -> None:
        data = self._document.read(base, size)
        # Defer: the request is raised mid-paint inside the model, so fill on the next loop turn.
        QTimer.singleShot(0, lambda: self.page_ready.emit(base, data))

    def write(self, addr: int, data: bytes) -> None:
        ok = self._document.write(addr, bytes(data))
        error = "" if ok else "address is outside the file's data"
        self.write_done.emit(addr, len(data), ok, error)
        if ok:
            self.changed.emit()


class BufferSource(QObject):
    """Serves a fixed, read-only set of (address, bytes) segments to a hex pane.

    Used by the side-by-side compare view, where each half shows an immutable snapshot (the device
    bytes that were read, or the file bytes they were compared against). Writes are ignored.
    """

    page_ready = Signal(int, object)  # (base, bytes)

    def __init__(self, segments: list[tuple[int, bytes]], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._segments = [(addr, bytes(data)) for addr, data in segments]

    def request_page(self, base: int, size: int) -> None:
        out = bytearray([0xFF]) * size  # padding outside the segments is never displayed
        for addr, data in self._segments:
            lo = max(base, addr)
            hi = min(base + size, addr + len(data))
            if lo < hi:
                out[lo - base : hi - base] = data[lo - addr : hi - addr]
        page = bytes(out)
        QTimer.singleShot(0, lambda: self.page_ready.emit(base, page))

    def write(self, addr: int, data: bytes) -> None:
        pass  # read-only
