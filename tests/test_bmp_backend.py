"""End-to-end tests for the BMP backend driving the fake-gdb subprocess (no hardware)."""

from __future__ import annotations

import struct

import pytest

from alynprog.core.backend import (
    ConnectOptions,
    EraseKind,
    EraseSpec,
    RegionKind,
    VerifyMethod,
)
from alynprog.core.errors import GdbError, NotSupportedError
from alynprog.core.image import FirmwareImage


def test_connect_applies_interface_speed_and_tpwr(make_bmp_backend):
    sent: list[str] = []

    def log(stream, text):
        if stream == "command":
            sent.append(text)

    options = ConnectOptions(interface_speed_hz=2_000_000, target_power=True, tpwr_delay_ms=0)
    make_bmp_backend("bmp-v2", options=options, log_callback=log)
    commands = "\n".join(sent)
    assert "frequency 2000000" in commands
    assert "tpwr enable" in commands


def test_connect_scan_attach_and_memory_map(make_bmp_backend):
    backend = make_bmp_backend("bmp-v2")
    targets = backend.scan_targets()
    assert [t.index for t in targets] == [1, 2]
    assert targets[0].name == "STM32F40x M4"

    backend.attach(1)
    regions = backend.memory_map()
    kinds = {r.kind for r in regions}
    assert RegionKind.FLASH in kinds
    assert RegionKind.RAM in kinds
    flash = next(r for r in regions if r.kind is RegionKind.FLASH)
    assert flash.blocksize == 0x800  # parsed from 'info mem' blocksize


def test_capabilities_reflect_v2_firmware(make_bmp_backend):
    backend = make_bmp_backend("bmp-v2")
    backend.attach(1)  # erase commands are target-bound -> visible only after attach
    caps = backend.capabilities()
    assert caps.supports_mass_erase
    assert caps.supports_sector_erase
    assert caps.supports_target_power
    assert caps.supports_register_access
    assert caps.verify_method is VerifyMethod.COMPARE_SECTIONS


def test_capabilities_reflect_no_erase_firmware(make_bmp_backend):
    backend = make_bmp_backend("bmp-no-erase")
    backend.attach(1)
    caps = backend.capabilities()
    assert not caps.supports_mass_erase
    assert not caps.supports_sector_erase


def test_erase_capabilities_appear_after_attach(make_bmp_backend):
    """Real BMP registers erase_mass/erase_range only after attach; capabilities must follow."""
    backend = make_bmp_backend("bmp-real-v2")
    before = backend.capabilities()
    assert not before.supports_mass_erase
    assert not before.supports_sector_erase

    backend.attach(1)
    after = backend.capabilities()
    assert after.supports_mass_erase
    assert after.supports_sector_erase


def test_read_memory(make_bmp_backend):
    backend = make_bmp_backend("bmp-v2")
    backend.attach(1)
    data = backend.read_memory(0x2000_0000, 4)
    assert data == bytes([0x00, 0x01, 0x02, 0x03])


def test_ram_write_round_trips(make_bmp_backend):
    backend = make_bmp_backend("bmp-v2")
    backend.attach(1)
    backend.write_memory(0x2000_0010, b"\xaa\xbb")
    assert backend.read_memory(0x2000_0010, 2) == b"\xaa\xbb"


def test_raw_flash_write_raises(make_bmp_backend):
    backend = make_bmp_backend("bmp-v2")
    backend.attach(1)
    with pytest.raises(GdbError):
        backend._write_raw_memory(0x0800_0000, b"\x00")


def test_flash_read_modify_write(make_bmp_backend):
    # The fake-gdb applies downloads to a flash overlay, so a flash edit reads back changed while
    # the rest of the sector is preserved.
    backend = make_bmp_backend("bmp-v2")
    backend.attach(1)
    addr = 0x0800_0000 + 0x40
    before_neighbour = backend.read_memory(addr + 1, 1)

    backend.write_memory(addr, b"\x5a")  # routes through sector read-modify-write
    assert backend.read_memory(addr, 1) == b"\x5a"
    assert backend.read_memory(addr + 1, 1) == before_neighbour  # neighbour preserved


def test_flash_rmw_halts_running_target(make_bmp_backend):
    # After resuming the core, a flash edit must halt it first (no erase while running).
    backend = make_bmp_backend("bmp-v2")
    backend.attach(1)
    # Resume the core without detaching (stays attached but running), unlike monitor reset.
    backend._session.execute("-exec-continue")
    backend._running = True

    backend.write_memory(0x0800_0040, b"\x5a")  # RMW must interrupt before erasing
    assert backend._running is False
    assert backend.read_memory(0x0800_0040, 1) == b"\x5a"


def test_program_reports_progress_and_verifies(make_bmp_backend, tmp_path):
    backend = make_bmp_backend("bmp-v2")
    backend.attach(1)

    bin_path = tmp_path / "fw.bin"
    bin_path.write_bytes(struct.pack("<4I", 1, 2, 3, 4))
    image = FirmwareImage.load(bin_path, base_addr=0x0800_0000)

    seen: list[tuple[int, int]] = []
    result = backend.program(
        image, progress=lambda done, total: seen.append((done, total)), verify=True
    )

    assert seen  # +download progress was parsed and forwarded
    assert seen[-1][0] == seen[-1][1]  # finished at 100%
    assert result.verified is True
    assert result.sections and result.sections[0].matched


def _bin_image(tmp_path):
    p = tmp_path / "fw.bin"
    p.write_bytes(struct.pack("<4I", 1, 2, 3, 4))
    return FirmwareImage.load(p, base_addr=0x0800_0000)


def test_program_without_run_after_stays_attached(make_bmp_backend, tmp_path):
    backend = make_bmp_backend("bmp-v2")
    backend.attach(1)
    assert backend.is_attached()
    backend.program(_bin_image(tmp_path), verify=False, run_after=False)
    assert backend.is_attached()  # no 'monitor reset' -> still attached, halted at entry


def test_program_run_after_detaches(make_bmp_backend, tmp_path):
    backend = make_bmp_backend("bmp-v2")
    backend.attach(1)
    backend.program(_bin_image(tmp_path), verify=False, run_after=True)
    assert not backend.is_attached()  # 'monitor reset' resets+runs and detaches


def test_reset_detaches_on_bmp(make_bmp_backend):
    backend = make_bmp_backend("bmp-v2")
    backend.attach(1)
    assert backend.is_attached()
    backend.reset()
    assert not backend.is_attached()


def test_mass_erase_supported_on_v2(make_bmp_backend):
    backend = make_bmp_backend("bmp-v2")
    backend.attach(1)
    backend.erase(EraseSpec(kind=EraseKind.MASS))  # should not raise


def test_mass_erase_unsupported_raises(make_bmp_backend):
    backend = make_bmp_backend("bmp-no-erase")
    backend.attach(1)
    with pytest.raises(NotSupportedError):
        backend.erase(EraseSpec(kind=EraseKind.MASS))


def test_read_registers(make_bmp_backend):
    backend = make_bmp_backend("bmp-v2")
    backend.attach(1)
    regs = backend.read_registers()
    assert "pc" in regs
    assert "sp" in regs


def test_raw_monitor_passthrough(make_bmp_backend):
    backend = make_bmp_backend("bmp-v2")
    text = backend.raw_monitor("version")
    assert "Black Magic" in text


def test_real_v2_profile_monitor_over_target_stream(make_bmp_backend):
    """Regression for a real BMP v2.0.0: monitor output arrives on the target stream (@); scan/
    attach work, and erase becomes available post-attach (target-specific commands)."""
    backend = make_bmp_backend("bmp-real-v2")

    # Monitor output is on the target stream; scan must still parse the target list.
    targets = backend.scan_targets()
    assert [t.index for t in targets] == [1, 2]

    backend.attach(1)
    assert backend.read_memory(0x2000_0000, 4) == bytes([0x00, 0x01, 0x02, 0x03])

    caps = backend.capabilities()
    assert caps.supports_mass_erase
    assert caps.supports_sector_erase
    assert caps.supports_target_power
    assert caps.supports_connect_under_reset

    backend.erase(EraseSpec(kind=EraseKind.MASS))  # available after attach
