"""Live pyOCD backend tests (CMSIS-DAP / ST-Link / J-Link / picoprobe). Mirrors the BMP live tests.

Skipped unless pyOCD is installed and a pyOCD-visible probe is attached. Configure via env:

  ALYNPROG_PYOCD_UID            probe unique id (optional; the first probe is used otherwise)
  ALYNPROG_PYOCD_TARGET         pyOCD target name (default: stm32f103cb — this rig's chip)
  ALYNPROG_PYOCD_ELF            flashable image for the destructive round-trip (default: the repo's
                                elf-test/test_board_stm32f103.elf, if present)
  ALYNPROG_ALLOW_FLASH_WRITE=1  opt in to the DESTRUCTIVE program test (off by default)

Picking the right target matters: pyOCD has no auto-detect and its only *builtin* F103 target is the
high-density ``stm32f103rc``. A medium-density chip (the common blue-pill C8/CB) needs its CMSIS
pack (``pyocd pack install stm32f103cb``) and the matching target, or programming fails.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from alynprog.backends.pyocd._api import pyocd_available
from alynprog.backends.pyocd.backend import PyocdBackend
from alynprog.backends.pyocd.discovery import discover_pyocd_probes
from alynprog.core.backend import ConnectOptions, RegionKind, VerifyMethod
from alynprog.core.image import FirmwareImage

pytestmark = pytest.mark.hardware

_DEFAULT_TARGET = "stm32f103cb"
_DEFAULT_ELF = Path(__file__).resolve().parents[2] / "elf-test" / "test_board_stm32f103.elf"


def _target() -> str:
    return os.environ.get("ALYNPROG_PYOCD_TARGET") or _DEFAULT_TARGET


def _probe():
    if not pyocd_available():
        return None
    probes = discover_pyocd_probes()
    uid = os.environ.get("ALYNPROG_PYOCD_UID")
    if uid:
        probes = [p for p in probes if p.identifier == uid]
    return probes[0] if probes else None


@pytest.fixture
def attached_backend():
    if not pyocd_available():
        pytest.skip("pyocd not installed")
    probe = _probe()
    if probe is None:
        pytest.skip("no pyOCD-visible probe attached (set ALYNPROG_PYOCD_UID)")
    backend = PyocdBackend.create()
    backend.connect(probe, ConnectOptions(target_name=_target(), interface_speed_hz=2_000_000))
    backend.attach(1)
    yield backend
    backend.close()


def test_discover_sees_probe():
    if not pyocd_available():
        pytest.skip("pyocd not installed")
    probes = discover_pyocd_probes()
    if not probes:
        pytest.skip("no pyOCD-visible probe attached")
    print("probes:", [(p.identifier, p.display_name) for p in probes])
    assert all(p.backend == "pyocd" for p in probes)


def test_capabilities(attached_backend):
    caps = attached_backend.capabilities()
    assert not caps.needs_external_binary
    assert caps.verify_method is VerifyMethod.NONE


def test_memory_map_has_flash(attached_backend):
    regions = attached_backend.memory_map()
    print("memory map:", regions)
    assert any(r.kind is RegionKind.FLASH for r in regions)


def test_read_flash_words(attached_backend):
    flash = next(r for r in attached_backend.memory_map() if r.kind is RegionKind.FLASH)
    data = attached_backend.read_memory(flash.start, 16)
    print("first 16 flash bytes:", data.hex())
    assert len(data) == 16


def test_reset(attached_backend):
    attached_backend.reset(halt=True)  # non-destructive
    attached_backend.reset(halt=False)


@pytest.mark.slow
def test_program_elf_roundtrip(attached_backend):
    """DESTRUCTIVE: flashes the ELF and reads it back. Opt in with ALYNPROG_ALLOW_FLASH_WRITE=1.

    The backend does no host-side verification (pyOCD owns programming), so this test does its own
    readback comparison of the ELF's flash PT_LOAD segments.
    """
    if os.environ.get("ALYNPROG_ALLOW_FLASH_WRITE") != "1":
        pytest.skip("set ALYNPROG_ALLOW_FLASH_WRITE=1 to allow a real flash write")
    elf_path = Path(os.environ.get("ALYNPROG_PYOCD_ELF") or _DEFAULT_ELF)
    if not elf_path.is_file():
        pytest.skip(f"no flashable ELF (set ALYNPROG_PYOCD_ELF; default {_DEFAULT_ELF})")

    from elftools.elf.elffile import ELFFile

    with open(elf_path, "rb") as fh:
        segments = [
            (segment["p_paddr"], segment.data()[: segment["p_filesz"]])
            for segment in ELFFile(fh).iter_segments()
            if segment["p_type"] == "PT_LOAD"
            and segment["p_paddr"] >= 0x0800_0000
            and segment["p_filesz"] > 0
        ]
    assert segments, "ELF has no flashable PT_LOAD segments"

    seen: list[tuple[int, int]] = []
    result = attached_backend.program(
        FirmwareImage.load(str(elf_path)), progress=lambda d, t: seen.append((d, t))
    )
    assert result.bytes_written > 0
    assert seen and seen[-1][0] == seen[-1][1]  # progress finished at 100%

    for base, expected in segments:
        assert attached_backend.read_memory(base, len(expected)) == expected
    attached_backend.reset(halt=False)  # run the freshly-flashed firmware
