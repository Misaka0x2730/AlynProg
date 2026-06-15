"""Integration tests for the main window's firmware-file menu wiring."""

from __future__ import annotations

import pytest

from alynprog.ui import main_window as mw_mod
from alynprog.ui.main_window import MainWindow
from alynprog.ui.theme import ThemeManager


@pytest.fixture
def window(qtbot, qapp, temp_settings):
    win = MainWindow(ThemeManager(qapp), temp_settings, use_fake=True)
    qtbot.addWidget(win)
    yield win
    win._session.shutdown()


def test_open_path_adds_tab_and_records_recent(window, temp_settings, tmp_path):
    p = tmp_path / "fw.bin"
    p.write_bytes(bytes(range(16)))

    window._open_path(str(p))

    assert window._workarea.count() == 2
    assert window._workarea.tabText(1) == "fw.bin"
    assert str(p) in temp_settings.recent_files
    # File-specific actions light up once a file tab is current.
    assert window._save_action.isEnabled()
    assert window._close_tab_action.isEnabled()


def test_tab_context_menu_open_opens_a_file(window, temp_settings, tmp_path, monkeypatch):
    p = tmp_path / "fw.bin"
    p.write_bytes(bytes(8))
    monkeypatch.setattr(
        mw_mod.QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(p), ""))
    )
    window._workarea.openRequested.emit()  # what the tab-strip context menu triggers
    assert window._workarea.count() == 2
    assert window._workarea.tabText(1) == "fw.bin"


def test_open_invalid_file_logs_and_adds_no_tab(window, tmp_path):
    missing = tmp_path / "nope.bin"
    window._open_path(str(missing))
    assert window._workarea.count() == 1  # only the device tab
    assert not window._save_action.isEnabled()


def test_file_actions_disabled_on_device_tab(window, tmp_path):
    p = tmp_path / "fw.bin"
    p.write_bytes(b"\x00\x01\x02\x03")
    window._open_path(str(p))
    window._workarea.setCurrentIndex(0)  # back to the device tab
    assert not window._save_action.isEnabled()
    assert not window._close_tab_action.isEnabled()


def test_recent_menu_skips_missing_files(window, temp_settings, tmp_path):
    present = tmp_path / "here.hex"
    present.write_text(":00000001FF\n")
    temp_settings.push_recent_file(str(tmp_path / "gone.hex"))
    temp_settings.push_recent_file(str(present))

    window._rebuild_recent_menu()
    labels = [a.text() for a in window._recent_menu.actions()]
    assert str(present) in labels
    assert str(tmp_path / "gone.hex") not in labels


def test_open_recent_referenced(window):
    # The module exposes the open filter the dialog uses; a light guard against accidental edits.
    assert "*.elf" in mw_mod._OPEN_FILTER


def test_nav_rail_shows_icons_and_switches_pages(window):
    strip = window._tabstrip
    assert strip.count() == 2
    # Icon-only rail: no visible text, names kept as tooltips, every row carries a rendered glyph.
    assert [strip.item(i).text() for i in range(2)] == ["", ""]
    assert [strip.item(i).toolTip() for i in range(2)] == ["Memory", "Programming"]
    assert all(not strip.item(i).icon().isNull() for i in range(2))

    assert window._pages.currentIndex() == 0
    strip.setCurrentRow(1)
    assert window._pages.currentIndex() == 1  # Programming page


def test_nav_icons_retint_on_palette_change(window):
    from PySide6.QtCore import QEvent

    before = window._tabstrip.item(0).icon().cacheKey()
    # A palette flip (theme switch) must rebuild the baked glyphs so they match the new text colour.
    window.changeEvent(QEvent(QEvent.Type.PaletteChange))
    after = window._tabstrip.item(0).icon().cacheKey()
    assert before != after
