"""Integration tests for SessionController driving the in-process fake backend (no hardware)."""

from __future__ import annotations

import struct

import pytest

from alynprog.core.backend import ConnectOptions, EraseKind, EraseSpec, ProbeInfo
from alynprog.core.image import FirmwareImage
from alynprog.core.session import SessionController, SessionState


@pytest.fixture
def controller(qtbot):
    ctrl = SessionController()
    yield ctrl
    ctrl.shutdown()


def _fake_probe() -> ProbeInfo:
    return ProbeInfo(backend="fake", identifier="fake-0", display_name="Simulated")


def _connect(controller, qtbot) -> None:
    with qtbot.waitSignal(controller.capabilities_changed, timeout=4000):
        controller.connect_probe("fake", _fake_probe(), ConnectOptions())
    assert controller.state is SessionState.CONNECTED


def _attach(controller, qtbot) -> None:
    _connect(controller, qtbot)
    with qtbot.waitSignal(controller.targets_found, timeout=4000) as scan:
        controller.scan()
    with qtbot.waitSignal(controller.memory_map_changed, timeout=4000):
        controller.attach(scan.args[0][0].index)
    assert controller.state is SessionState.ATTACHED


def test_connect_scan_attach(controller, qtbot):
    _attach(controller, qtbot)


def test_failed_connect_returns_to_disconnected(controller, qtbot):
    probe = ProbeInfo(backend="bmp", identifier="/dev/null", display_name="bmp")
    with qtbot.waitSignal(controller.operation_failed, timeout=4000) as failure:
        controller.connect_probe("bmp", probe, ConnectOptions(), gdb_path="")
    assert failure.args[0] == "connect"
    assert controller.state is SessionState.DISCONNECTED


def test_read_page(controller, qtbot):
    _attach(controller, qtbot)
    with qtbot.waitSignal(controller.memory_page_ready, timeout=4000) as page:
        controller.read_page(0x2000_0000, 256)
    base, data = page.args
    assert base == 0x2000_0000
    assert len(data) == 256


def test_ram_write_round_trip(controller, qtbot):
    _attach(controller, qtbot)
    with qtbot.waitSignal(controller.memory_written, timeout=4000) as write:
        controller.write_bytes(0x2000_0010, b"\xca\xfe")
    addr, length, ok, error = write.args
    assert (addr, length, ok) == (0x2000_0010, 2, True)
    assert error == ""


def test_flash_write_uses_read_modify_write(controller, qtbot):
    # With RMW, editing a flash byte succeeds (the sector is read, patched and reprogrammed).
    _attach(controller, qtbot)
    with qtbot.waitSignal(controller.memory_written, timeout=8000) as write:
        controller.write_bytes(0x0800_0010, b"\x5a")
    addr, length, ok, error = write.args
    assert (addr, length, ok) == (0x0800_0010, 1, True)
    assert error == ""


def test_program_verifies(controller, qtbot, tmp_path):
    _attach(controller, qtbot)
    bin_path = tmp_path / "fw.bin"
    bin_path.write_bytes(struct.pack("<4I", 1, 2, 3, 4))

    with qtbot.waitSignal(controller.program_done, timeout=8000) as done:
        controller.program(str(bin_path), 0x0800_0000, verify=True, run_after=False)
    result = done.args[0]
    assert result.verified is True


def test_program_without_run_after_stays_attached(controller, qtbot, tmp_path):
    _attach(controller, qtbot)
    bin_path = tmp_path / "fw.bin"
    bin_path.write_bytes(struct.pack("<4I", 1, 2, 3, 4))
    with qtbot.waitSignal(controller.operation_succeeded, timeout=8000):
        controller.program(str(bin_path), 0x0800_0000, verify=False, run_after=False)
    assert controller.state is SessionState.ATTACHED


def test_run_after_program_returns_to_connected_and_clears_ui(controller, qtbot, tmp_path):
    _attach(controller, qtbot)
    bin_path = tmp_path / "fw.bin"
    bin_path.write_bytes(struct.pack("<4I", 1, 2, 3, 4))
    cleared: dict[str, object] = {}
    controller.targets_found.connect(lambda t: cleared.__setitem__("targets", t))
    controller.memory_map_changed.connect(lambda m: cleared.__setitem__("map", m))

    with qtbot.waitSignal(controller.operation_succeeded, timeout=8000):
        controller.program(str(bin_path), 0x0800_0000, verify=False, run_after=True)

    # Detached: back to the "connected, before scan" state with target/memory UI cleared.
    assert controller.state is SessionState.CONNECTED
    assert cleared.get("targets") == []
    assert cleared.get("map") == []


def test_mass_erase(controller, qtbot):
    _attach(controller, qtbot)
    with qtbot.waitSignal(controller.operation_succeeded, timeout=4000) as ok:
        controller.erase(EraseSpec(kind=EraseKind.MASS))
    assert ok.args[0] == "erase"
    assert controller.state is SessionState.ATTACHED


def test_state_returns_to_base_after_busy(controller, qtbot):
    _attach(controller, qtbot)
    states: list[SessionState] = []
    controller.state_changed.connect(states.append)
    with qtbot.waitSignal(controller.operation_succeeded, timeout=4000):
        controller.reset(halt=False)
    assert SessionState.BUSY in states
    assert controller.state is SessionState.ATTACHED


def test_image_loads_for_program(tmp_path):
    # Guard the worker's image-loading path independently of Qt.
    p = tmp_path / "fw.bin"
    p.write_bytes(b"\x01\x02\x03\x04")
    image = FirmwareImage.load(p, 0x0800_0000)
    assert image.total_size == 4
