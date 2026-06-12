"""Widget tests for the side-by-side comparison result tab."""

from __future__ import annotations

from PySide6.QtCore import Qt

from alynprog.core.compare import MemoryCompareResult, compare_segment
from alynprog.ui.panels.compare_tab import CompareTab


def _result(expected, actual, base=0x0800_0000):
    return MemoryCompareResult((compare_segment(expected, actual, base),))


def test_panes_sized_and_diff_highlighted(qapp, qtbot):
    tab = CompareTab(_result(b"\x11\x22\x33\x44", b"\x11\xff\x33\x44"), "fw.bin")
    qtbot.addWidget(tab)

    assert tab._device._model.region.size == 4
    assert tab._file._model.region.size == 4

    bg = Qt.ItemDataRole.BackgroundRole
    # The byte that differs (offset 1) is tinted in both halves; the rest is not.
    assert tab._device._model.data(tab._device._model.index(0, 1), bg) is not None
    assert tab._file._model.data(tab._file._model.index(0, 1), bg) is not None
    assert tab._device._model.data(tab._device._model.index(0, 0), bg) is None
    assert tab._next_btn.isEnabled()
    assert "differ" in tab._summary.text()


def test_left_is_device_right_is_file(qapp, qtbot):
    # actual = device bytes (left), expected = file bytes (right).
    tab = CompareTab(_result(b"\x11\x22\x33\x44", b"\x11\xff\x33\x44"), "fw.bin")
    qtbot.addWidget(tab)

    di = tab._device._model.index(0, 1)
    fi = tab._file._model.index(0, 1)
    tab._device._model.data(di)  # first touch schedules the (deferred) page fill
    tab._file._model.data(fi)
    qtbot.wait(30)

    assert tab._device._model.data(di) == "FF"  # device side shows the read-back byte
    assert tab._file._model.data(fi) == "22"  # file side shows the file byte


def test_matched_result_has_no_navigation(qapp, qtbot):
    tab = CompareTab(_result(b"\x01\x02", b"\x01\x02", base=0x2000_0000), "fw.bin")
    qtbot.addWidget(tab)
    assert not tab._next_btn.isEnabled()
    assert "match" in tab._summary.text().lower()


def test_title_helper(qapp):
    assert CompareTab.title_for("fw.bin") == "Compare: fw.bin"
