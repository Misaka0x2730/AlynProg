"""Discover Black Magic Probes via pyserial and pick each probe's GDB-server port.

A BMP enumerates as a composite USB device (VID:PID ``1d50:6018``) exposing two CDC-ACM ports:
interface 0 is the GDB server (what we drive), interface 2 is the target UART. Identifying
interface 0 reliably differs per OS:

* **Windows** — the GDB port's hardware id contains ``MI_00``.
* **Linux** — pyserial's ``location`` ends with the interface number (``…:1.0``).
* **macOS** — device names (``/dev/cu.usbmodemXXXX1`` vs ``…3``) only sort correctly; this is
  treated as *ambiguous* so the UI can offer a manual override.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Protocol

from serial.tools import list_ports

from alynprog.core.backend import ProbeInfo

BMP_VID = 0x1D50
BMP_PID = 0x6018


class _PortLike(Protocol):
    device: str
    vid: int | None
    pid: int | None
    serial_number: str | None
    location: str | None
    hwid: str
    name: str | None


def discover_bmp_probes(ports: list[_PortLike] | None = None) -> list[ProbeInfo]:
    """Return one :class:`ProbeInfo` per discovered probe (identifier = its GDB port device)."""
    all_ports = list(ports) if ports is not None else list(list_ports.comports())
    bmp_ports = [p for p in all_ports if (p.vid, p.pid) == (BMP_VID, BMP_PID)]

    grouped: dict[str, list[_PortLike]] = defaultdict(list)
    for port in bmp_ports:
        grouped[port.serial_number or ""].append(port)

    probes: list[ProbeInfo] = []
    for serial, group in grouped.items():
        gdb_port, ambiguous = _pick_gdb_port(group)
        if gdb_port is None:
            continue
        short = serial[-8:] if serial else gdb_port.device
        probes.append(
            ProbeInfo(
                backend="bmp",
                identifier=gdb_port.device,
                display_name=f"Black Magic Probe ({short})",
                serial=serial,
                port_ambiguous=ambiguous,
            )
        )
    probes.sort(key=lambda p: p.identifier)
    return probes


def _pick_gdb_port(group: list[_PortLike]) -> tuple[_PortLike | None, bool]:
    """Return ``(gdb_port, ambiguous)`` for a single probe's set of ports."""
    if not group:
        return None, False
    if len(group) == 1:
        return group[0], False

    # Windows: positively identify interface 0 by its MI_00 hardware id.
    for port in group:
        hwid = (port.hwid or "").upper()
        name = (getattr(port, "name", "") or "").upper()
        if "MI_00" in hwid or "MI_00" in name:
            return port, False

    # Linux: pyserial location ends with the interface number, e.g. "1-1.4:1.0".
    for port in group:
        location = port.location or ""
        if location.endswith(".0") or location.endswith(":1.0"):
            return port, False

    # macOS / fallback: the lowest-sorted device name is interface 0, but we cannot be certain.
    ordered = sorted(group, key=lambda p: p.device)
    return ordered[0], True
