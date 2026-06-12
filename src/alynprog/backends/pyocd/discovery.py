"""Discover pyOCD-compatible probes (CMSIS-DAP / ST-Link / J-Link / picoprobe).

Mirrors the BMP discovery shape: a module-level function returning one :class:`ProbeInfo` per probe,
so tests can pass a fake :class:`PyocdApi` while ``PyocdBackend.discover`` calls it with the real
one. Returns an empty list when pyOCD is not installed (the app then simply shows no pyOCD probes).
"""

from __future__ import annotations

from alynprog.backends.pyocd._api import PyocdApi, pyocd_available
from alynprog.core.backend import ProbeInfo


def discover_pyocd_probes(api: PyocdApi | None = None) -> list[ProbeInfo]:
    if api is None:
        if not pyocd_available():
            return []
        api = PyocdApi.real()

    probes = api.get_all_connected_probes(blocking=False, print_wait_message=False)
    out: list[ProbeInfo] = []
    for probe in probes:
        uid = str(getattr(probe, "unique_id", "") or "")
        if not uid:
            continue
        vendor = (getattr(probe, "vendor_name", "") or "").strip()
        product = (getattr(probe, "product_name", "") or "").strip()
        label = " ".join(part for part in (vendor, product) if part) or "pyOCD probe"
        short = uid[-8:] if len(uid) > 8 else uid
        out.append(
            ProbeInfo(
                backend="pyocd",
                identifier=uid,
                display_name=f"{label} ({short})",
                serial=uid,
            )
        )
    out.sort(key=lambda info: info.identifier)
    return out
