"""M6: pyOCD UI integration — ConnectPanel row toggling/gating + ProgramPanel verify gating.

All offscreen and hardware-free: ``discover_all`` is monkeypatched to inject a fake pyOCD probe,
and connect is intercepted so the real backend/worker is never driven.
"""

from __future__ import annotations

import pytest

from alynprog.core.backend import ProbeCapabilities, ProbeInfo, TargetInfo, VerifyMethod
from alynprog.core.session import SessionController, SessionState
from alynprog.ui.panels import connect_panel as connect_panel_mod
from alynprog.ui.panels.connect_panel import ConnectPanel
from alynprog.ui.panels.program_panel import ProgramPanel


@pytest.fixture
def controller(qtbot):
    ctrl = SessionController()
    yield ctrl
    ctrl.shutdown()


def _pyocd_probe():
    return ProbeInfo(
        backend="pyocd",
        identifier="UID123",
        display_name="Fake STLink (UID123)",
        serial="UID123",
    )


def _bmp_probe():
    return ProbeInfo(
        backend="bmp", identifier="/dev/ttyACM0", display_name="BMP", serial="BMPSER"
    )


def _panel(qtbot, controller, settings, monkeypatch, probes):
    monkeypatch.setattr(connect_panel_mod, "discover_all", lambda **_kwargs: probes)
    panel = ConnectPanel(controller, settings, use_fake=False)
    qtbot.addWidget(panel)
    return panel


# --- ConnectPanel row visibility --------------------------------------------


def test_pyocd_probe_hides_classic_rows_shows_target(qtbot, controller, temp_settings, monkeypatch):
    panel = _panel(qtbot, controller, temp_settings, monkeypatch, [_pyocd_probe()])
    assert not panel._target_wrap.isHidden()
    assert panel._port_override.isHidden()
    assert panel._gdb_wrap.isHidden()
    assert panel._tpwr_wrap.isHidden()


def test_bmp_probe_shows_classic_rows_hides_target(qtbot, controller, temp_settings, monkeypatch):
    panel = _panel(qtbot, controller, temp_settings, monkeypatch, [_bmp_probe()])
    assert panel._target_wrap.isHidden()
    assert not panel._port_override.isHidden()
    assert not panel._gdb_wrap.isHidden()
    assert not panel._tpwr_wrap.isHidden()


# --- connect gating & target carry ------------------------------------------


def test_connect_disabled_until_target_chosen(qtbot, controller, temp_settings, monkeypatch):
    panel = _panel(qtbot, controller, temp_settings, monkeypatch, [_pyocd_probe()])
    panel._update_for_state(SessionState.DISCONNECTED)
    assert not panel._connect_btn.isEnabled()  # pyocd probe, no target yet

    panel._chosen_target = "stm32f103rc"
    panel._update_connect_enabled()
    assert panel._connect_btn.isEnabled()


def test_remembered_target_is_prefilled(qtbot, controller, temp_settings, monkeypatch):
    temp_settings.set_pyocd_last_target("UID123", "stm32f103rc")
    panel = _panel(qtbot, controller, temp_settings, monkeypatch, [_pyocd_probe()])
    assert panel._chosen_target == "stm32f103rc"
    assert panel._target_field.text() == "stm32f103rc"
    panel._update_for_state(SessionState.DISCONNECTED)
    assert panel._connect_btn.isEnabled()  # a prefilled target unblocks Connect


def test_connect_options_carry_target_name(qtbot, controller, temp_settings, monkeypatch):
    panel = _panel(qtbot, controller, temp_settings, monkeypatch, [_pyocd_probe()])
    panel._chosen_target = "stm32f103rc"
    captured: dict = {}
    monkeypatch.setattr(
        controller,
        "connect_probe",
        lambda backend, probe, options, gdb: captured.update(backend=backend, options=options),
    )
    panel._on_connect()
    assert captured["backend"] == "pyocd"
    assert captured["options"].target_name == "stm32f103rc"


def test_bmp_connect_has_no_target_name(qtbot, controller, temp_settings, monkeypatch):
    panel = _panel(qtbot, controller, temp_settings, monkeypatch, [_bmp_probe()])
    captured: dict = {}
    monkeypatch.setattr(
        controller,
        "connect_probe",
        lambda backend, probe, options, gdb: captured.update(options=options),
    )
    panel._on_connect()
    assert captured["options"].target_name == ""


def test_port_override_ignored_for_pyocd(qtbot, controller, temp_settings, monkeypatch):
    panel = _panel(qtbot, controller, temp_settings, monkeypatch, [_pyocd_probe()])
    panel._port_override.setText("/dev/whatever")  # hidden row, but must also be ignored
    probe = panel._selected_probe()
    assert probe.identifier == "UID123"  # not replaced by the override


def test_port_override_still_applies_for_bmp(qtbot, controller, temp_settings, monkeypatch):
    panel = _panel(qtbot, controller, temp_settings, monkeypatch, [_bmp_probe()])
    panel._port_override.setText("/dev/custom")
    probe = panel._selected_probe()
    assert probe.identifier == "/dev/custom"


# --- ProgramPanel verify gating ---------------------------------------------


def test_verify_checkbox_disabled_for_no_verify_then_restored(qtbot, controller, temp_settings):
    panel = ProgramPanel(controller, temp_settings)
    qtbot.addWidget(panel)

    panel._apply_capabilities(ProbeCapabilities(verify_method=VerifyMethod.NONE))
    assert not panel._verify.isEnabled()
    assert not panel._verify.isChecked()

    # Switching to a verifying backend re-enables AND restores the user's preference (default True).
    panel._apply_capabilities(ProbeCapabilities(verify_method=VerifyMethod.READBACK))
    assert panel._verify.isEnabled()
    assert panel._verify.isChecked()


def test_verify_checkbox_reset_on_disconnect(qtbot, controller, temp_settings):
    panel = ProgramPanel(controller, temp_settings)
    qtbot.addWidget(panel)
    panel._apply_capabilities(ProbeCapabilities(verify_method=VerifyMethod.NONE))
    assert not panel._verify.isEnabled()

    panel._update_for_state(SessionState.DISCONNECTED)
    assert panel._verify.isEnabled()
    assert panel._verify.isChecked()


def test_choose_target_disabled_unless_disconnected(qtbot, controller, temp_settings, monkeypatch):
    panel = _panel(qtbot, controller, temp_settings, monkeypatch, [_pyocd_probe()])
    panel._update_for_state(SessionState.DISCONNECTED)
    assert panel._choose_target_btn.isEnabled()
    panel._update_for_state(SessionState.ATTACHED)
    assert not panel._choose_target_btn.isEnabled()  # can't refetch catalog during a live session


def test_connect_disabled_when_no_probes(qtbot, controller, temp_settings, monkeypatch):
    panel = _panel(qtbot, controller, temp_settings, monkeypatch, [])
    panel._update_for_state(SessionState.DISCONNECTED)
    assert not panel._connect_btn.isEnabled()  # "No probes found" -> nothing to connect to


# --- pyOCD auto-attach + targets box ----------------------------------------


def test_targets_box_hidden_for_pyocd(qtbot, controller, temp_settings, monkeypatch):
    panel = _panel(qtbot, controller, temp_settings, monkeypatch, [_pyocd_probe()])
    assert panel._targets_box.isHidden()  # one target, attached automatically -> list is pointless


def test_targets_box_visible_for_bmp(qtbot, controller, temp_settings, monkeypatch):
    panel = _panel(qtbot, controller, temp_settings, monkeypatch, [_bmp_probe()])
    assert not panel._targets_box.isHidden()


def test_panel_is_vertically_non_greedy(qtbot, controller, temp_settings, monkeypatch):
    from PySide6.QtWidgets import QSizePolicy

    # The panel sizes to its content so the Target configuration dock fits it (and the stacked Log
    # dock takes the freed space when the Targets box is hidden for pyOCD).
    panel = _panel(qtbot, controller, temp_settings, monkeypatch, [_pyocd_probe()])
    assert panel.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Maximum


def test_pyocd_panel_prefers_less_height_than_bmp(qtbot, controller, temp_settings, monkeypatch):
    bmp_panel = _panel(qtbot, controller, temp_settings, monkeypatch, [_bmp_probe()])
    pyocd_panel = _panel(qtbot, controller, temp_settings, monkeypatch, [_pyocd_probe()])
    # Hiding the Targets box + status line makes the pyOCD panel's preferred height much smaller.
    assert pyocd_panel.sizeHint().height() < bmp_panel.sizeHint().height()


def test_status_line_hidden_for_pyocd(qtbot, controller, temp_settings, monkeypatch):
    # The window's status bar already shows session state, so the panel's status line is redundant.
    panel = _panel(qtbot, controller, temp_settings, monkeypatch, [_pyocd_probe()])
    assert panel._status.isHidden()


def test_status_line_visible_for_bmp(qtbot, controller, temp_settings, monkeypatch):
    panel = _panel(qtbot, controller, temp_settings, monkeypatch, [_bmp_probe()])
    assert not panel._status.isHidden()


def test_pyocd_connect_auto_attaches_single_target(qtbot, controller, temp_settings, monkeypatch):
    panel = _panel(qtbot, controller, temp_settings, monkeypatch, [_pyocd_probe()])
    panel._chosen_target = "stm32f103cb"
    monkeypatch.setattr(controller, "connect_probe", lambda *a: None)
    attached: list[int] = []
    monkeypatch.setattr(controller, "attach", lambda index: attached.append(index))

    panel._on_connect()
    assert panel._pending_autoattach is True
    panel._populate_targets([TargetInfo(index=1, name="stm32f103cb")])  # the post-connect scan
    assert attached == [1]
    assert panel._pending_autoattach is False


def test_bmp_connect_does_not_auto_attach(qtbot, controller, temp_settings, monkeypatch):
    panel = _panel(qtbot, controller, temp_settings, monkeypatch, [_bmp_probe()])
    monkeypatch.setattr(controller, "connect_probe", lambda *a: None)
    attached: list[int] = []
    monkeypatch.setattr(controller, "attach", lambda index: attached.append(index))

    panel._on_connect()
    assert panel._pending_autoattach is False
    panel._populate_targets([TargetInfo(index=1, name="a"), TargetInfo(index=2, name="b")])
    assert attached == []  # BMP keeps the manual target list


def test_pyocd_attach_failure_rolls_back_to_disconnected(
    qtbot, controller, temp_settings, monkeypatch
):
    panel = _panel(qtbot, controller, temp_settings, monkeypatch, [_pyocd_probe()])
    rolled_back: list[bool] = []
    monkeypatch.setattr(controller, "disconnect_probe", lambda: rolled_back.append(True))
    # A failed auto-attach (e.g. target unplugged from the probe) rolls back so Connect re-enables.
    panel._on_operation_failed("attach", "No target processor found")
    assert rolled_back == [True]


def test_pyocd_non_attach_failure_keeps_connection(qtbot, controller, temp_settings, monkeypatch):
    panel = _panel(qtbot, controller, temp_settings, monkeypatch, [_pyocd_probe()])
    rolled_back: list[bool] = []
    monkeypatch.setattr(controller, "disconnect_probe", lambda: rolled_back.append(True))
    panel._on_operation_failed("program", "flash failed")
    assert rolled_back == []  # only a failed attach rolls back


def test_bmp_attach_failure_does_not_roll_back(qtbot, controller, temp_settings, monkeypatch):
    panel = _panel(qtbot, controller, temp_settings, monkeypatch, [_bmp_probe()])
    rolled_back: list[bool] = []
    monkeypatch.setattr(controller, "disconnect_probe", lambda: rolled_back.append(True))
    panel._on_operation_failed("attach", "no target")
    assert rolled_back == []  # BMP stays connected; retry via its visible Attach button
