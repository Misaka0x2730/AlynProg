"""Widget-level tests for capability/state gating in the panels (offscreen, no hardware)."""

from __future__ import annotations

import pytest

from alynprog.core.backend import ConnectOptions, ProbeInfo
from alynprog.core.session import SessionController, SessionState
from alynprog.ui.panels.connect_panel import ConnectPanel
from alynprog.ui.panels.program_panel import ProgramPanel


@pytest.fixture
def controller(qtbot):
    ctrl = SessionController()
    yield ctrl
    ctrl.shutdown()


def _attach_fake(controller, qtbot) -> None:
    probe = ProbeInfo(backend="fake", identifier="fake-0", display_name="Simulated")
    with qtbot.waitSignal(controller.capabilities_changed, timeout=4000):
        controller.connect_probe("fake", probe, ConnectOptions())
    with qtbot.waitSignal(controller.targets_found, timeout=4000) as scan:
        controller.scan()
    with qtbot.waitSignal(controller.memory_map_changed, timeout=4000):
        controller.attach(scan.args[0][0].index)


def test_program_panel_sectors_and_erase_after_attach(qtbot, controller, temp_settings):
    panel = ProgramPanel(controller, temp_settings)
    qtbot.addWidget(panel)

    # Before attach: erase gated off, no sectors listed.
    assert not panel._mass_erase_btn.isEnabled()
    assert panel._sector_list.count() == 0

    _attach_fake(controller, qtbot)
    qtbot.wait(50)

    # Fake flash is 1 MiB with 16 KiB sectors -> 64 sectors; erase becomes available.
    assert panel._sector_list.count() == 64
    assert panel._mass_erase_btn.isEnabled()
    assert panel._erase_sectors_btn.isEnabled()


def test_program_panel_erase_selected_sectors(qtbot, controller, temp_settings):
    from PySide6.QtCore import Qt

    panel = ProgramPanel(controller, temp_settings)
    qtbot.addWidget(panel)
    _attach_fake(controller, qtbot)
    qtbot.wait(50)

    for row in (0, 1):  # two contiguous sectors
        panel._sector_list.item(row).setCheckState(Qt.CheckState.Checked)
    assert len(panel._checked_sectors()) == 2

    with qtbot.waitSignal(controller.operation_succeeded, timeout=4000):
        panel._on_erase_selected_sectors()


def test_program_panel_base_addr_only_for_bin(qtbot, controller, temp_settings):
    panel = ProgramPanel(controller, temp_settings)
    qtbot.addWidget(panel)

    panel._file_box.setCurrentText("firmware.elf")
    assert not panel._base_addr.isEnabled()
    panel._file_box.setCurrentText("firmware.bin")
    assert panel._base_addr.isEnabled()


def test_connect_panel_buttons_follow_state(qtbot, controller, temp_settings):
    panel = ConnectPanel(controller, temp_settings, use_fake=True)
    qtbot.addWidget(panel)

    panel._update_for_state(SessionState.DISCONNECTED)
    assert panel._connect_btn.isEnabled()
    assert not panel._scan_btn.isEnabled()

    panel._update_for_state(SessionState.ATTACHED)
    assert not panel._connect_btn.isEnabled()
    assert panel._scan_btn.isEnabled()
    assert panel._attach_btn.isEnabled()


def test_hexview_ascii_highlight_follows_selection(qtbot, controller, temp_settings):
    from alynprog.ui.panels.hexview import HexView

    view = HexView(controller)
    qtbot.addWidget(view)
    _attach_fake(controller, qtbot)
    qtbot.wait(50)

    # Select the hex cell at row 2, column 3 (width 1) -> ASCII byte offset 3 highlighted.
    view._table.setCurrentIndex(view._model.index(2, 3))
    assert view._model.ascii_highlight() == (2, 3, 4)

    # Selecting the ASCII column itself clears the per-char highlight.
    view._table.setCurrentIndex(view._model.index(2, view._model.ascii_column()))
    assert view._model.ascii_highlight()[0] == -1


def test_connect_panel_double_click_attaches(qtbot, controller, temp_settings):
    panel = ConnectPanel(controller, temp_settings, use_fake=True)
    qtbot.addWidget(panel)

    probe = ProbeInfo(backend="fake", identifier="fake-0", display_name="Simulated")
    with qtbot.waitSignal(controller.capabilities_changed, timeout=4000):
        controller.connect_probe("fake", probe, ConnectOptions())
    with qtbot.waitSignal(controller.targets_found, timeout=4000):
        controller.scan()
    qtbot.wait(20)
    assert panel._targets.count() >= 1

    with qtbot.waitSignal(controller.memory_map_changed, timeout=4000):
        panel._on_target_double_clicked(panel._targets.item(0))
    assert controller.state is SessionState.ATTACHED


def test_hexview_refreshes_after_program(qtbot, controller, temp_settings):
    from alynprog.ui.panels.hexview import HexView

    view = HexView(controller)
    qtbot.addWidget(view)
    _attach_fake(controller, qtbot)
    qtbot.wait(50)

    # Cache a page, then a completed program must drop the stale cache so memory is re-read.
    model = view._model
    model.data(model.index(0, 0))
    qtbot.wait(100)
    assert len(model._cache) >= 1
    controller.program_done.emit(object())
    assert len(model._cache) == 0


def test_connect_panel_auto_scans_after_connect(qtbot, controller, temp_settings):
    panel = ConnectPanel(controller, temp_settings, use_fake=True)
    qtbot.addWidget(panel)

    probe = ProbeInfo(backend="fake", identifier="fake-0", display_name="Simulated")
    # No manual Scan click — connecting should auto-populate the target list.
    with qtbot.waitSignal(controller.targets_found, timeout=4000):
        controller.connect_probe("fake", probe, ConnectOptions())
    qtbot.wait(20)
    assert panel._targets.count() >= 1


def test_connect_panel_clears_targets_on_disconnect(qtbot, controller, temp_settings):
    panel = ConnectPanel(controller, temp_settings, use_fake=True)
    qtbot.addWidget(panel)

    probe = ProbeInfo(backend="fake", identifier="fake-0", display_name="Simulated")
    with qtbot.waitSignal(controller.targets_found, timeout=4000):
        controller.connect_probe("fake", probe, ConnectOptions())  # auto-scans
    qtbot.wait(20)
    assert panel._targets.count() >= 1

    # Disconnecting must clear the stale target list.
    with qtbot.waitSignal(controller.targets_found, timeout=4000):
        controller.disconnect_probe()
    qtbot.wait(20)
    assert panel._targets.count() == 0


def test_connect_panel_tpwr_delay_visibility(qtbot, controller, temp_settings):
    panel = ConnectPanel(controller, temp_settings, use_fake=True)
    qtbot.addWidget(panel)

    panel._tpwr.setChecked(False)
    assert panel._tpwr_delay.isHidden()  # delay hidden when target power is off
    panel._tpwr.setChecked(True)
    assert not panel._tpwr_delay.isHidden()  # delay shown when target power is on


def test_connect_panel_interface_speed_to_hz(qtbot, controller, temp_settings):
    panel = ConnectPanel(controller, temp_settings, use_fake=True)
    qtbot.addWidget(panel)

    assert panel._speed.singleStep() == 100  # 100 kHz steps
    # Transport combo and speed spinbox (stacked in the form) share one width.
    assert panel._transport.maximumWidth() == panel._speed.maximumWidth() > 0
    panel._speed.setValue(1000)  # 1 MHz
    assert panel._speed_hz() == 1_000_000
    panel._speed.setValue(10)  # 10 kHz (range minimum)
    assert panel._speed_hz() == 10_000
    panel._speed.setValue(500)
    assert panel._speed_hz() == 500_000


def test_connect_panel_lists_fake_probe(qtbot, controller, temp_settings):
    panel = ConnectPanel(controller, temp_settings, use_fake=True)
    qtbot.addWidget(panel)
    backends = {
        panel._probe_box.itemData(i).backend
        for i in range(panel._probe_box.count())
        if panel._probe_box.itemData(i) is not None
    }
    assert "fake" in backends


def test_hexview_goto_remembers_recent_addresses(qtbot, controller, temp_settings):
    from alynprog.ui.panels.hexview import HexView

    view = HexView(controller, temp_settings)
    qtbot.addWidget(view)
    _attach_fake(controller, qtbot)
    qtbot.wait(50)

    # Navigate to an address inside the fake flash region (starts at 0x08000000).
    view._goto.setEditText("0x08000100")
    view._on_goto()
    assert "0x08000100" in temp_settings.recent_goto_addresses
    assert "0x08000100" in [view._goto.itemText(i) for i in range(view._goto.count())]

    # A second address goes to the front of the recents dropdown.
    view._goto.setEditText("0x08000200")
    view._on_goto()
    assert temp_settings.recent_goto_addresses[0] == "0x08000200"

    # An invalid address is reported but not remembered.
    view._goto.setEditText("not-hex")
    view._on_goto()
    assert "not-hex" not in temp_settings.recent_goto_addresses
