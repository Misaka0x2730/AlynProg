"""Tests for the user-adjustable hex-view font size: the controller, live re-fonting and wiring."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent

from alynprog.core.session import SessionController
from alynprog.ui.panels.compare_tab import CompareTab
from alynprog.ui.panels.hexview import HexView
from alynprog.ui.panels.hexview.font import (
    MAX_POINT_SIZE,
    MIN_POINT_SIZE,
    HexFontController,
    default_point_size,
)
from alynprog.ui.panels.workarea import MemoryWorkArea
from tests.test_compare_tab import _result


@pytest.fixture
def controller(qtbot):
    ctrl = SessionController()
    yield ctrl
    ctrl.shutdown()


# --- HexFontController ---------------------------------------------------------


def test_default_size_is_14pt_and_in_range(qapp):
    font = HexFontController()
    assert font.point_size == 14
    assert default_point_size() == 14
    assert MIN_POINT_SIZE <= font.point_size <= MAX_POINT_SIZE


def test_set_point_size_clamps_to_range(qapp):
    font = HexFontController()
    font.set_point_size(1000)
    assert font.point_size == MAX_POINT_SIZE
    font.set_point_size(0)
    assert font.point_size == MIN_POINT_SIZE


def test_change_emits_once_and_persists(qapp, temp_settings):
    font = HexFontController(temp_settings)
    seen: list[int] = []
    font.sizeChanged.connect(seen.append)

    font.set_point_size(20)
    assert seen == [20]
    assert temp_settings.hex_font_point_size == 20

    # Setting the same value is a no-op: no second emission, no thrash.
    font.set_point_size(20)
    assert seen == [20]


def test_stored_size_is_restored(qapp, temp_settings):
    temp_settings.hex_font_point_size = 18
    assert HexFontController(temp_settings).point_size == 18


def test_step_and_reset(qapp):
    font = HexFontController()
    font.set_point_size(14)
    font.step(2)
    assert font.point_size == 16
    font.step(-3)
    assert font.point_size == 13
    font.reset()
    assert font.point_size == default_point_size()


# --- panes follow the controller ----------------------------------------------


def test_pane_uses_controller_size_at_construction(qtbot, controller, temp_settings):
    font = HexFontController(temp_settings)
    font.set_point_size(19)
    view = HexView(controller, temp_settings, font)
    qtbot.addWidget(view)
    assert view._table.font().pointSize() == 19


def test_size_change_refonts_all_panes_and_grows_rows(qtbot, controller, temp_settings):
    font = HexFontController(temp_settings)
    font.set_point_size(12)
    device = HexView(controller, temp_settings, font)
    area = MemoryWorkArea(device, controller, temp_settings, font)
    qtbot.addWidget(area)

    before = device._table.verticalHeader().defaultSectionSize()
    font.set_point_size(24)

    assert device._table.font().pointSize() == 24
    # A taller font yields a taller row so the larger glyphs are not clipped.
    assert device._table.verticalHeader().defaultSectionSize() > before


def test_compare_panes_follow_controller(qtbot, temp_settings):
    font = HexFontController(temp_settings)
    font.set_point_size(15)
    result = _result(b"\x11\x22", b"\x11\xff")
    tab = CompareTab(result, "Device memory", "fw.bin", temp_settings, font)
    qtbot.addWidget(tab)
    assert tab._left._table.font().pointSize() == 15
    font.set_point_size(21)
    assert tab._left._table.font().pointSize() == 21
    assert tab._right._table.font().pointSize() == 21


def test_pane_without_controller_uses_default(qtbot, controller):
    view = HexView(controller)
    qtbot.addWidget(view)
    assert view._table.font().pointSize() == default_point_size()


# --- Ctrl+wheel zoom -----------------------------------------------------------


def _ctrl_wheel(delta_y: int) -> QWheelEvent:
    return QWheelEvent(
        QPointF(0, 0),
        QPointF(0, 0),
        QPoint(0, 0),
        QPoint(0, delta_y),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.ControlModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def test_ctrl_wheel_zooms_through_controller(qtbot, controller, temp_settings):
    font = HexFontController(temp_settings)
    font.set_point_size(14)
    view = HexView(controller, temp_settings, font)
    qtbot.addWidget(view)

    assert view.eventFilter(view._table.viewport(), _ctrl_wheel(120)) is True
    assert font.point_size == 15
    view.eventFilter(view._table.viewport(), _ctrl_wheel(-120))
    assert font.point_size == 14


def test_plain_wheel_is_not_consumed(qtbot, controller, temp_settings):
    view = HexView(controller, temp_settings)
    qtbot.addWidget(view)
    plain = QWheelEvent(
        QPointF(0, 0),
        QPointF(0, 0),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    # Without Ctrl the table keeps its normal scroll behaviour (filter does not eat the event).
    assert view.eventFilter(view._table.viewport(), plain) is False


# --- main-window status-bar slider --------------------------------------------


def test_status_bar_slider_drives_and_reflects_the_font(qtbot, qapp, temp_settings):
    from alynprog.ui.main_window import MainWindow
    from alynprog.ui.theme import ThemeManager

    win = MainWindow(ThemeManager(qapp), temp_settings, use_fake=True)
    qtbot.addWidget(win)
    try:
        assert win._zoom_slider.value() == win._hex_font.point_size

        # Moving the slider re-fonts the device view and persists the choice.
        win._zoom_slider.setValue(22)
        assert win._hex_font.point_size == 22
        assert win._hexview._table.font().pointSize() == 22
        assert temp_settings.hex_font_point_size == 22

        # A zoom action steps the size and the slider follows it back.
        win._hex_font.step(-1)
        assert win._zoom_slider.value() == 21
    finally:
        win._session.shutdown()
