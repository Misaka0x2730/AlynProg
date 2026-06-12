"""Widget-level tests for firmware-file tabs: open, edit, save, and close lifecycle."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from alynprog.core.backend import ConnectOptions, ProbeInfo
from alynprog.core.document import FirmwareDocument
from alynprog.core.image import ImageKind
from alynprog.core.session import SessionController
from alynprog.ui.panels import file_tab as file_tab_mod
from alynprog.ui.panels.base_address_dialog import BaseAddressDialog
from alynprog.ui.panels.compare_tab import CompareTab
from alynprog.ui.panels.hexview import HexView
from alynprog.ui.panels.workarea import MemoryWorkArea
from tests.test_elf import _build_elf


@pytest.fixture
def controller(qtbot):
    ctrl = SessionController()
    yield ctrl
    ctrl.shutdown()


def _workarea(qtbot, controller, settings) -> MemoryWorkArea:
    view = HexView(controller, settings)
    area = MemoryWorkArea(view, controller, settings)
    qtbot.addWidget(area)
    return area


def _open_bin(area, tmp_path, data=b"\x00\x01\x02\x03\x04\x05\x06\x07"):
    p = tmp_path / "fw.bin"
    p.write_bytes(data)
    return area.open_document(FirmwareDocument.open(p)), p


def _edit_first_byte(tab, text="FF") -> None:
    tab._model.setData(tab._model.index(0, 0), text, Qt.ItemDataRole.EditRole)


def _attach(controller, qtbot) -> None:
    probe = ProbeInfo(backend="fake", identifier="fake-0", display_name="Simulated")
    with qtbot.waitSignal(controller.capabilities_changed, timeout=4000):
        controller.connect_probe("fake", probe, ConnectOptions())
    with qtbot.waitSignal(controller.targets_found, timeout=4000) as scan:
        controller.scan()
    with qtbot.waitSignal(controller.memory_map_changed, timeout=4000):
        controller.attach(scan.args[0][0].index)


# --- opening ------------------------------------------------------------------


def test_open_bin_adds_tab_with_filename_and_region(qtbot, controller, temp_settings, tmp_path):
    area = _workarea(qtbot, controller, temp_settings)
    tab, _p = _open_bin(area, tmp_path)
    assert area.count() == 2
    assert area.tabText(1) == "fw.bin"
    assert area.current_file_tab() is tab
    assert tab._model.region is not None
    assert tab._model.region.size == 8


def test_file_bytes_are_editable(qtbot, controller, temp_settings, tmp_path):
    area = _workarea(qtbot, controller, temp_settings)
    tab, _p = _open_bin(area, tmp_path)
    # An UNKNOWN-kind file region would be read-only for the device; the file override flips that.
    assert bool(tab._model.flags(tab._model.index(0, 0)) & Qt.ItemFlag.ItemIsEditable)


# --- editing / dirty ----------------------------------------------------------


def test_edit_marks_dirty_and_stars_title(qtbot, controller, temp_settings, tmp_path):
    area = _workarea(qtbot, controller, temp_settings)
    tab, _p = _open_bin(area, tmp_path)
    assert not tab._save_btn.isEnabled()

    _edit_first_byte(tab)
    assert tab.document.dirty is True
    assert tab.title() == "fw.bin*"
    assert area.tabText(1) == "fw.bin*"
    assert tab._save_btn.isEnabled()


# --- saving -------------------------------------------------------------------


def test_save_writes_bytes_and_clears_dirty(qtbot, controller, temp_settings, tmp_path):
    area = _workarea(qtbot, controller, temp_settings)
    tab, p = _open_bin(area, tmp_path)
    _edit_first_byte(tab)
    assert tab.save() is True
    assert tab.document.dirty is False
    assert area.tabText(1) == "fw.bin"
    assert p.read_bytes()[0] == 0xFF


def test_save_as_exports_and_records_recent(
    qtbot, controller, temp_settings, tmp_path, monkeypatch
):
    area = _workarea(qtbot, controller, temp_settings)
    tab, _p = _open_bin(area, tmp_path)
    _edit_first_byte(tab)

    out = tmp_path / "exported.hex"
    monkeypatch.setattr(
        file_tab_mod.QFileDialog,
        "getSaveFileName",
        staticmethod(lambda *a, **k: (str(out), "")),
    )
    assert tab.save_as() is True
    assert out.is_file()
    assert tab.document.kind is ImageKind.HEX
    assert str(out) in temp_settings.recent_files


def test_save_as_cancelled_keeps_dirty(qtbot, controller, temp_settings, tmp_path, monkeypatch):
    area = _workarea(qtbot, controller, temp_settings)
    tab, _p = _open_bin(area, tmp_path)
    _edit_first_byte(tab)
    monkeypatch.setattr(
        file_tab_mod.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", ""))
    )
    assert tab.save_as() is False
    assert tab.document.dirty is True


def test_elf_save_falls_through_to_save_as(qtbot, controller, temp_settings, tmp_path, monkeypatch):
    src = tmp_path / "fw.elf"
    src.write_bytes(_build_elf([(0x0800_0000, 0x0800_0000, b"\x11\x22\x33\x44")]))
    area = _workarea(qtbot, controller, temp_settings)
    tab = area.open_document(FirmwareDocument.open(src))
    assert tab.document.supports_in_place_save is False

    out = tmp_path / "exported.hex"
    monkeypatch.setattr(
        file_tab_mod.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), ""))
    )
    assert tab.save() is True  # Save with no in-place support exports instead
    assert tab.document.kind is ImageKind.HEX
    assert tab.document.supports_in_place_save is True


# --- closing ------------------------------------------------------------------


def test_close_discard_drops_tab(qtbot, controller, temp_settings, tmp_path, monkeypatch):
    area = _workarea(qtbot, controller, temp_settings)
    tab, _p = _open_bin(area, tmp_path)
    _edit_first_byte(tab)
    monkeypatch.setattr(
        file_tab_mod.QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Discard),
    )
    assert area.close_file_tab(tab) is True
    assert area.count() == 1


def test_close_cancel_keeps_tab(qtbot, controller, temp_settings, tmp_path, monkeypatch):
    area = _workarea(qtbot, controller, temp_settings)
    tab, _p = _open_bin(area, tmp_path)
    _edit_first_byte(tab)
    monkeypatch.setattr(
        file_tab_mod.QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel),
    )
    assert area.close_file_tab(tab) is False
    assert area.count() == 2


def test_close_save_writes_then_drops_tab(qtbot, controller, temp_settings, tmp_path, monkeypatch):
    area = _workarea(qtbot, controller, temp_settings)
    tab, p = _open_bin(area, tmp_path)
    _edit_first_byte(tab)
    monkeypatch.setattr(
        file_tab_mod.QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Save),
    )
    assert area.close_file_tab(tab) is True
    assert area.count() == 1
    assert p.read_bytes()[0] == 0xFF


def test_clean_tab_closes_without_prompt(qtbot, controller, temp_settings, tmp_path):
    area = _workarea(qtbot, controller, temp_settings)
    tab, _p = _open_bin(area, tmp_path)
    assert area.close_file_tab(tab) is True  # not dirty -> no QMessageBox
    assert area.count() == 1


def test_device_tab_is_not_closable(qtbot, controller, temp_settings, tmp_path):
    area = _workarea(qtbot, controller, temp_settings)
    _open_bin(area, tmp_path)
    area._on_close_requested(0)  # a close request aimed at the device tab is ignored
    assert area.count() == 2


# --- tab context menu ---------------------------------------------------------


def test_context_menu_on_device_tab_offers_only_open(qtbot, controller, temp_settings):
    area = _workarea(qtbot, controller, temp_settings)
    menu = area._build_context_menu(0)  # the pinned device tab
    labels = [a.text() for a in menu.actions() if a.text()]
    assert labels == ["Open File…"]


def test_context_menu_open_emits_request(qtbot, controller, temp_settings):
    area = _workarea(qtbot, controller, temp_settings)
    requested: list[int] = []
    area.openRequested.connect(lambda: requested.append(1))
    menu = area._build_context_menu(0)
    open_action = next(a for a in menu.actions() if a.text() == "Open File…")
    open_action.trigger()
    assert requested == [1]


def test_context_menu_on_file_tab_can_close(qtbot, controller, temp_settings, tmp_path):
    area = _workarea(qtbot, controller, temp_settings)
    _open_bin(area, tmp_path)
    menu = area._build_context_menu(1)  # the file tab
    labels = [a.text() for a in menu.actions() if a.text()]
    assert "Open File…" in labels
    assert "Close" in labels

    close_action = next(a for a in menu.actions() if a.text() == "Close")
    close_action.trigger()  # clean tab -> closes without prompting
    assert area.count() == 1


# --- compare ------------------------------------------------------------------


def _open_diff_bin(area, tmp_path):
    # Fake flash is erased (0xFF); a byte that is not 0xFF (offset 2) will show up as a difference.
    p = tmp_path / "fw.bin"
    p.write_bytes(b"\xff\xff\x00\xff")
    return area.open_document(FirmwareDocument.open(p))


def _run_compare(area, tab, qtbot, controller, base=0x0800_0000):
    tab._ask_base_address = lambda: base  # skip the modal base-address dialog
    with qtbot.waitSignal(controller.compare_done, timeout=4000):
        tab.compare()
    qtbot.wait(20)
    return area.currentWidget()


def test_compare_disabled_until_attached(qtbot, controller, temp_settings, tmp_path):
    area = _workarea(qtbot, controller, temp_settings)
    tab, _p = _open_bin(area, tmp_path)
    assert not tab._compare_btn.isEnabled()
    _attach(controller, qtbot)
    qtbot.wait(50)
    assert tab._compare_btn.isEnabled()


def test_compare_opens_split_result_tab(qtbot, controller, temp_settings, tmp_path):
    area = _workarea(qtbot, controller, temp_settings)
    _attach(controller, qtbot)
    qtbot.wait(50)
    tab = _open_diff_bin(area, tmp_path)

    compare = _run_compare(area, tab, qtbot, controller)
    # Device tab + file tab + the new compare tab.
    assert area.count() == 3
    assert isinstance(compare, CompareTab)
    assert area.tabText(area.indexOf(compare)).startswith("Compare:")

    bg = Qt.ItemDataRole.BackgroundRole
    # The differing byte (offset 2) is tinted in BOTH halves; the matching byte in neither.
    assert compare._device._model.data(compare._device._model.index(0, 2), bg) is not None
    assert compare._file._model.data(compare._file._model.index(0, 2), bg) is not None
    assert compare._device._model.data(compare._device._model.index(0, 0), bg) is None
    assert compare._next_btn.isEnabled()


def test_compare_progress_dialog_is_dismissed_when_done(qtbot, controller, temp_settings, tmp_path):
    area = _workarea(qtbot, controller, temp_settings)
    _attach(controller, qtbot)
    qtbot.wait(50)
    tab = _open_diff_bin(area, tmp_path)
    _run_compare(area, tab, qtbot, controller)
    # The modal progress dialog is created during the compare and closed once it finishes.
    assert tab._progress_dlg is None


def test_compare_cancel_calls_session(qtbot, controller, temp_settings, tmp_path, monkeypatch):
    area = _workarea(qtbot, controller, temp_settings)
    _attach(controller, qtbot)
    qtbot.wait(50)
    tab = _open_diff_bin(area, tmp_path)
    cancelled: list[int] = []
    monkeypatch.setattr(controller, "cancel_compare", lambda: cancelled.append(1))
    tab._cancel_compare()
    assert cancelled == [1]


def test_compare_match_opens_tab_with_no_diffs(qtbot, controller, temp_settings, tmp_path):
    area = _workarea(qtbot, controller, temp_settings)
    _attach(controller, qtbot)
    qtbot.wait(50)
    p = tmp_path / "fw.bin"
    p.write_bytes(b"\xff\xff\xff\xff")  # matches erased flash
    tab = area.open_document(FirmwareDocument.open(p))

    compare = _run_compare(area, tab, qtbot, controller)
    assert isinstance(compare, CompareTab)
    assert not compare._next_btn.isEnabled()  # nothing to step through
    bg = Qt.ItemDataRole.BackgroundRole
    assert compare._device._model.data(compare._device._model.index(0, 0), bg) is None


def test_compare_panes_scroll_and_width_stay_in_sync(qtbot, controller, temp_settings, tmp_path):
    area = _workarea(qtbot, controller, temp_settings)
    _attach(controller, qtbot)
    qtbot.wait(50)
    tab = _open_diff_bin(area, tmp_path)
    compare = _run_compare(area, tab, qtbot, controller)

    # Changing the cell width on one half mirrors onto the other.
    compare._device._width_box.setCurrentIndex(2)  # 32-bit
    assert compare._file._width_box.currentIndex() == 2


def test_compare_tab_is_closable(qtbot, controller, temp_settings, tmp_path):
    area = _workarea(qtbot, controller, temp_settings)
    _attach(controller, qtbot)
    qtbot.wait(50)
    tab = _open_diff_bin(area, tmp_path)
    compare = _run_compare(area, tab, qtbot, controller)
    assert area._close_tab_at(area.indexOf(compare)) is True  # read-only, closes without a prompt
    assert area.count() == 2


def test_compare_outside_map_is_skipped_without_prompt(
    qtbot, controller, temp_settings, tmp_path, monkeypatch
):
    area = _workarea(qtbot, controller, temp_settings)
    _attach(controller, qtbot)
    qtbot.wait(50)
    tab = _open_diff_bin(area, tmp_path)
    # 0x4000_0000 is outside the fake flash/RAM map -> nothing overlaps, so compare is a no-op.
    # A real QMessageBox here would hang the test, proving no modal prompt is raised.
    monkeypatch.setattr(tab, "_ask_base_address", lambda: 0x4000_0000)
    tab.compare()
    assert tab._comparing is False
    assert area.count() == 2  # no compare tab opened


def test_compare_clamps_to_overlapping_bytes(
    qtbot, controller, temp_settings, tmp_path, monkeypatch
):
    area = _workarea(qtbot, controller, temp_settings)
    _attach(controller, qtbot)
    qtbot.wait(50)
    # 16-byte file placed so its last 8 bytes run past the end of fake RAM (0x2000_0000+0x2_0000).
    p = tmp_path / "fw.bin"
    p.write_bytes(bytes(16))
    tab = area.open_document(FirmwareDocument.open(p))
    base = 0x2000_0000 + 0x2_0000 - 8
    monkeypatch.setattr(tab, "_ask_base_address", lambda: base)

    results: list = []
    controller.compare_done.connect(results.append)
    with qtbot.waitSignal(controller.compare_done, timeout=4000):
        tab.compare()  # no prompt despite the size overflow
    qtbot.wait(20)
    # Only the 8 in-RAM bytes were compared; the overhang is silently dropped.
    assert results[0].bytes_compared == 8


def test_base_address_dialog_parses_hex(qtbot):
    dialog = BaseAddressDialog(0x0800_0000)
    qtbot.addWidget(dialog)
    assert dialog.base_address() == 0x0800_0000
    dialog._edit.setText("not-hex")
    assert dialog.base_address() is None
    dialog._edit.setText("0x20000000")
    assert dialog.base_address() == 0x2000_0000
