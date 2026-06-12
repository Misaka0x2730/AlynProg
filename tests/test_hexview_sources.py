"""Unit tests for the file-document hex source (deferred fill, synchronous writes)."""

from __future__ import annotations

from alynprog.core.document import FirmwareDocument
from alynprog.ui.panels.hexview.sources import FileDocumentSource


def _doc(tmp_path, data=b"\x01\x02\x03\x04"):
    p = tmp_path / "fw.bin"
    p.write_bytes(data)
    return FirmwareDocument.open(p)


def test_request_page_is_deferred_then_emits(qapp, qtbot, tmp_path):
    source = FileDocumentSource(_doc(tmp_path))
    received: list[tuple[int, bytes]] = []
    source.page_ready.connect(lambda base, data: received.append((base, data)))

    source.request_page(0, 4)
    assert received == []  # nothing synchronous: the fill must wait for the event loop

    qtbot.waitUntil(lambda: len(received) == 1, timeout=1000)
    base, data = received[0]
    assert base == 0
    assert data == b"\x01\x02\x03\x04"


def test_write_updates_document_and_reports_ok(qapp, tmp_path):
    source = FileDocumentSource(_doc(tmp_path))
    results: list[tuple] = []
    changed: list[int] = []
    source.write_done.connect(lambda addr, length, ok, err: results.append((addr, length, ok, err)))
    source.changed.connect(lambda: changed.append(1))

    source.write(0x0000_0002, b"\xff")
    assert results == [(0x0000_0002, 1, True, "")]
    assert changed == [1]
    assert source.document.read(0, 4) == b"\x01\x02\xff\x04"
    assert source.document.dirty is True


def test_write_outside_data_reports_failure(qapp, tmp_path):
    source = FileDocumentSource(_doc(tmp_path))
    results: list[tuple] = []
    changed: list[int] = []
    source.write_done.connect(lambda addr, length, ok, err: results.append((addr, length, ok, err)))
    source.changed.connect(lambda: changed.append(1))

    source.write(0x100, b"\xff")
    assert results[0][2] is False
    assert changed == []  # a rejected write does not mark the document dirty
