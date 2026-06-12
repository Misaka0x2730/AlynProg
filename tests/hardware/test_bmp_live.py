"""Live Black Magic Probe tests validating the risky hardware assumptions from milestone M3.

Skipped unless a probe and GDB are actually present. Set ``ALYNPROG_BMP_PORT`` to force a specific
serial device and ``ALYNPROG_GDB`` to force a specific GDB executable.
"""

from __future__ import annotations

import os

import pytest

from alynprog.backends.bmp.backend import BmpBackend
from alynprog.backends.bmp.discovery import discover_bmp_probes
from alynprog.core.backend import ConnectOptions, ProbeInfo, RegionKind
from alynprog.tools.gdb_locator import find_gdb

pytestmark = pytest.mark.hardware


def _gdb_path() -> str | None:
    info = find_gdb(os.environ.get("ALYNPROG_GDB") or None)
    return info.path if info else None


def _probe() -> ProbeInfo | None:
    port = os.environ.get("ALYNPROG_BMP_PORT")
    if port:
        return ProbeInfo(backend="bmp", identifier=port, display_name=f"BMP ({port})")
    probes = discover_bmp_probes()
    return probes[0] if probes else None


@pytest.fixture
def connected_backend():
    gdb = _gdb_path()
    if gdb is None:
        pytest.skip("no GDB executable found (set ALYNPROG_GDB)")
    probe = _probe()
    if probe is None:
        pytest.skip("no Black Magic Probe found (set ALYNPROG_BMP_PORT)")
    logs: list[tuple[str, str]] = []
    backend = BmpBackend(gdb_path=gdb, log_callback=lambda s, t: logs.append((s, t)))
    backend.connect(probe, ConnectOptions())
    yield backend
    backend.close()


@pytest.fixture
def attached_backend(connected_backend):
    targets = connected_backend.scan_targets()
    if not targets:
        pytest.skip("probe connected but no target on the bus")
    connected_backend.attach(targets[0].index)
    return connected_backend


def test_connect_detects_dialect(connected_backend):
    caps = connected_backend.capabilities()
    assert caps.needs_external_binary
    # Dialect detection ran; at least the scan command is known.
    assert connected_backend._require_dialect().scan_command("swd")


def test_scan_lists_targets(connected_backend):
    targets = connected_backend.scan_targets()
    if not targets:
        pytest.skip("probe connected but no target on the bus")
    print("targets:", targets)


def test_attach_and_memory_map(attached_backend):
    regions = attached_backend.memory_map()
    print("memory map:", regions)
    assert any(r.kind is RegionKind.FLASH for r in regions)


def test_read_flash_words(attached_backend):
    flash = next(r for r in attached_backend.memory_map() if r.kind is RegionKind.FLASH)
    data = attached_backend.read_memory(flash.start, 16)
    print("first 16 flash bytes:", data.hex())
    assert len(data) == 16


@pytest.mark.slow
def test_raw_flash_write_is_rejected(attached_backend):
    """The low-level raw write path must still be rejected by GDB (non-destructive)."""
    from alynprog.core.errors import GdbError

    flash = next(r for r in attached_backend.memory_map() if r.kind is RegionKind.FLASH)
    with pytest.raises(GdbError) as excinfo:
        attached_backend._write_raw_memory(flash.start, b"\x00")
    print("raw flash write error text:", excinfo.value)


@pytest.mark.slow
def test_flash_read_modify_write_roundtrip(attached_backend):
    """DESTRUCTIVE: rewrites a flash sector. Opt in with ALYNPROG_ALLOW_FLASH_WRITE=1.

    Writes back the byte's current value, so the sector contents are unchanged (though the sector
    is physically erased and reprogrammed). Verifies the RMW path on real hardware end-to-end.
    """
    if os.environ.get("ALYNPROG_ALLOW_FLASH_WRITE") != "1":
        pytest.skip("set ALYNPROG_ALLOW_FLASH_WRITE=1 to allow a real flash rewrite")
    flash = next(r for r in attached_backend.memory_map() if r.kind is RegionKind.FLASH)
    addr = flash.start + 0x10
    original = attached_backend.read_memory(addr, 1)
    attached_backend.write_memory(addr, original)  # RMW; net content unchanged
    assert attached_backend.read_memory(addr, 1) == original
