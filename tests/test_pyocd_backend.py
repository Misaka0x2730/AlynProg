"""M4: pyOCD backend lifecycle — the ABC acid test, driven by a fake pyOCD API (no hardware)."""

from __future__ import annotations

import struct

import pytest

from alynprog.backends.pyocd._api import pyocd_available
from alynprog.backends.pyocd.backend import PyocdBackend
from alynprog.backends.pyocd.discovery import discover_pyocd_probes
from alynprog.core.backend import (
    ConnectOptions,
    EraseKind,
    EraseSpec,
    ProbeBackend,
    ProbeInfo,
    RegionKind,
    VerifyMethod,
)
from alynprog.core.errors import NotSupportedError, ProbeError, TargetError
from alynprog.core.image import FirmwareImage

pytestmark = pytest.mark.skipif(not pyocd_available(), reason="pyocd not installed")


def _fakes():
    from tests import fake_pyocd

    return fake_pyocd


def _attach(rig=None):
    """Build a PyocdBackend on a fake API, connect + attach it, return (backend, rig)."""
    fp = _fakes()
    rig = rig or fp.FakeRig()
    api = fp.make_fake_api(rig)
    backend = PyocdBackend(api=api)
    probe = discover_pyocd_probes(api)[0]
    backend.connect(probe, ConnectOptions(target_name="stm32f103rc"))
    backend.attach(1)
    return backend, rig


def _bin_image(tmp_path, data: bytes, base: int):
    path = tmp_path / "fw.bin"
    path.write_bytes(data)
    return FirmwareImage.load(path, base_addr=base)


# --- contract & connection ---------------------------------------------------


def test_is_probe_backend():
    assert issubclass(PyocdBackend, ProbeBackend)


def test_capabilities_advertise_no_verify_and_no_registers():
    backend, _ = _attach()
    caps = backend.capabilities()
    assert caps.verify_method is VerifyMethod.NONE
    assert caps.supports_register_access is False
    assert caps.needs_external_binary is False
    assert caps.supports_run_control is True
    assert "cortex-m" in caps.architectures


def test_connect_requires_a_target_name():
    fp = _fakes()
    api = fp.make_fake_api()
    backend = PyocdBackend(api=api)
    probe = discover_pyocd_probes(api)[0]
    with pytest.raises(TargetError):
        backend.connect(probe, ConnectOptions())  # no target_name


def test_connect_to_vanished_probe_raises_probe_error():
    fp = _fakes()
    api = fp.make_fake_api(probes=[fp.FakeDebugProbe(unique_id="AAA")])
    backend = PyocdBackend(api=api)
    ghost = ProbeInfo(backend="pyocd", identifier="ZZZ", display_name="x", serial="ZZZ")
    with pytest.raises(ProbeError):
        backend.connect(ghost, ConnectOptions(target_name="stm32f103rc"))


def test_scan_targets_echoes_chosen_target():
    backend, _ = _attach()
    targets = backend.scan_targets()
    assert [t.name for t in targets] == ["stm32f103rc"]


# --- memory ------------------------------------------------------------------


def test_memory_map_flash_and_ram():
    fp = _fakes()
    backend, _ = _attach()
    regions = backend.memory_map()
    flash = next(r for r in regions if r.start == fp.FLASH_BASE)
    ram = next(r for r in regions if r.start == fp.RAM_BASE)
    assert flash.kind is RegionKind.FLASH
    assert flash.blocksize == fp.FLASH_SECTOR
    assert ram.kind is RegionKind.RAM
    assert ram.blocksize == 0  # RAM reports no sector size (pyocd gives None -> 0)


def test_flash_reads_erased_and_ram_zero():
    fp = _fakes()
    backend, _ = _attach()
    assert backend.read_memory(fp.FLASH_BASE, 4) == b"\xff\xff\xff\xff"
    assert backend.read_memory(fp.RAM_BASE, 4) == b"\x00\x00\x00\x00"


def test_ram_write_round_trip():
    fp = _fakes()
    backend, _ = _attach()
    backend.write_memory(fp.RAM_BASE + 8, b"\xca\xfe")
    assert backend.read_memory(fp.RAM_BASE + 8, 2) == b"\xca\xfe"


def test_flash_write_is_read_modify_write():
    fp = _fakes()
    backend, _ = _attach()
    sector = fp.FLASH_BASE
    backend._program_flash_block(sector, b"\xaa" * 0x100 + b"\xff" * (fp.FLASH_SECTOR - 0x100))
    assert backend.read_memory(sector + 0x10, 1) == b"\xaa"

    backend.write_memory(sector + 0x10, b"\x5a")
    assert backend.read_memory(sector + 0x10, 1) == b"\x5a"
    assert backend.read_memory(sector + 0x11, 1) == b"\xaa"  # neighbours survive the RMW
    assert backend.read_memory(sector + 0x0F, 1) == b"\xaa"


# --- programming -------------------------------------------------------------


def test_program_bin_writes_flash_with_monotonic_progress(tmp_path):
    fp = _fakes()
    backend, _ = _attach()
    payload = struct.pack("<4I", 0xDEAD, 0xBEEF, 1, 2)
    image = _bin_image(tmp_path, payload, fp.FLASH_BASE)

    seen: list[tuple[int, int]] = []
    result = backend.program(image, progress=lambda d, t: seen.append((d, t)))

    assert result.verified is None  # no host-side verify
    assert result.bytes_written == len(payload)
    assert seen[-1] == (len(payload), len(payload))
    assert all(seen[i][0] <= seen[i + 1][0] for i in range(len(seen) - 1))  # monotonic
    assert backend.read_memory(fp.FLASH_BASE, len(payload)) == payload


def test_program_hex_writes_flash(tmp_path):
    from intelhex import IntelHex

    fp = _fakes()
    backend, _ = _attach()
    payload = b"\x11\x22\x33\x44"
    ih = IntelHex()
    for i, byte in enumerate(payload):
        ih[fp.FLASH_BASE + i] = byte
    hex_path = tmp_path / "fw.hex"
    ih.write_hex_file(str(hex_path))

    backend.program(FirmwareImage.load(hex_path))
    assert backend.read_memory(fp.FLASH_BASE, 4) == payload


def test_program_elf_reports_progress_without_verify(tmp_path):
    backend, _ = _attach()
    elf_path = tmp_path / "fw.elf"
    elf_path.write_bytes(b"\x7fELF" + b"\x00" * 60)  # load() keys off the extension; not parsed

    seen: list[tuple[int, int]] = []
    image = FirmwareImage.load(elf_path)
    result = backend.program(image, progress=lambda d, t: seen.append((d, t)))
    assert result.verified is None
    assert seen[-1][0] == seen[-1][1] == result.bytes_written


def test_run_after_detaches_then_reattach_works(tmp_path):
    fp = _fakes()
    backend, _ = _attach()
    image = _bin_image(tmp_path, b"\x01\x02\x03\x04", fp.FLASH_BASE)

    backend.program(image, run_after=True)
    assert backend.is_attached() is False  # mimic BMP: program-and-run detaches

    backend.attach(1)
    assert backend.is_attached() is True


# --- erase / reset -----------------------------------------------------------


def test_mass_erase_clears_flash(tmp_path):
    fp = _fakes()
    backend, _ = _attach()
    backend.program(_bin_image(tmp_path, b"\x01\x02\x03\x04", fp.FLASH_BASE))
    assert backend.read_memory(fp.FLASH_BASE, 4) == b"\x01\x02\x03\x04"

    backend.erase(EraseSpec(kind=EraseKind.MASS))
    assert backend.read_memory(fp.FLASH_BASE, 4) == b"\xff\xff\xff\xff"


def test_range_erase_aligns_to_whole_sectors(tmp_path):
    fp = _fakes()
    backend, _ = _attach()
    # Fill the first two sectors, then range-erase a few bytes inside the first one.
    backend.program(_bin_image(tmp_path, b"\x01" * (2 * fp.FLASH_SECTOR), fp.FLASH_BASE))

    backend.erase(EraseSpec(kind=EraseKind.RANGE, addr=fp.FLASH_BASE, length=4))
    # Flash erase is sector-granular (like real pyOCD): the whole first sector is erased, the
    # second sector survives.
    assert backend.read_memory(fp.FLASH_BASE, fp.FLASH_SECTOR) == b"\xff" * fp.FLASH_SECTOR
    assert backend.read_memory(fp.FLASH_BASE + fp.FLASH_SECTOR, 4) == b"\x01\x01\x01\x01"


def test_reset_halts_or_runs():
    backend, _ = _attach()
    backend.reset(halt=True)
    assert backend._target.get_state().name == "HALTED"
    backend.reset(halt=False)
    assert backend._target.get_state().name == "RUNNING"


# --- teardown & escape hatches ----------------------------------------------


def test_detach_then_reattach_then_close():
    backend, _ = _attach()
    assert backend.is_attached() is True
    backend.detach()
    assert backend.is_attached() is False
    backend.attach(1)
    assert backend.is_attached() is True
    backend.close()
    assert backend.is_attached() is False


def test_raw_monitor_not_supported():
    backend, _ = _attach()
    with pytest.raises(NotSupportedError):
        backend.raw_monitor("anything")


def test_registers_not_supported():
    backend, _ = _attach()
    with pytest.raises(NotSupportedError):
        backend.read_registers()


# --- error mapping (integration with the real pyOCD exception hierarchy) -----


def test_program_failure_maps_to_target_error(tmp_path):
    from pyocd.core import exceptions as px

    fp = _fakes()
    backend, _ = _attach(fp.FakeRig(program_error=px.FlashProgramFailure("write failed")))
    with pytest.raises(TargetError):
        backend.program(_bin_image(tmp_path, b"\x00\x01\x02\x03", fp.FLASH_BASE))


def test_transfer_fault_on_read_maps_to_target_error():
    from pyocd.core import exceptions as px

    fp = _fakes()
    backend, _ = _attach(fp.FakeRig(read_error=px.TransferFaultError("bus fault")))
    with pytest.raises(TargetError):
        backend.read_memory(fp.RAM_BASE, 4)


def test_probe_disconnected_on_read_maps_to_probe_error():
    from pyocd.core import exceptions as px

    fp = _fakes()
    backend, _ = _attach(fp.FakeRig(read_error=px.ProbeDisconnected("probe gone")))
    with pytest.raises(ProbeError):
        backend.read_memory(fp.RAM_BASE, 4)


def test_erase_failure_maps_to_target_error():
    from pyocd.core import exceptions as px

    fp = _fakes()
    backend, _ = _attach(fp.FakeRig(erase_error=px.FlashEraseFailure("erase failed")))
    with pytest.raises(TargetError):
        backend.erase(EraseSpec(kind=EraseKind.MASS))


def test_target_ops_before_attach_raise_clean_target_error():
    # Connected but NOT attached: self._target is None. Target ops must raise a clean TargetError
    # (whose message friendly_error maps to "Attach to a target first."), not a raw AttributeError.
    fp = _fakes()
    api = fp.make_fake_api()
    backend = PyocdBackend(api=api)
    backend.connect(discover_pyocd_probes(api)[0], ConnectOptions(target_name="stm32f103cb"))

    with pytest.raises(TargetError, match="not attached"):
        backend.reset(halt=False)
    with pytest.raises(TargetError, match="not attached"):
        backend.memory_map()
    with pytest.raises(TargetError, match="not attached"):
        backend.read_memory(fp.RAM_BASE, 4)
