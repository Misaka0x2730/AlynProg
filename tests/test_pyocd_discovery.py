"""M4: pyOCD probe discovery — field mapping and the pyocd-absent path."""

from __future__ import annotations

import pytest

from alynprog.backends.pyocd import discovery
from alynprog.backends.pyocd._api import pyocd_available
from alynprog.backends.pyocd.discovery import discover_pyocd_probes

pytestmark = pytest.mark.skipif(not pyocd_available(), reason="pyocd not installed")


def _fakes():
    # Imported lazily so this module still collects when pyocd is absent (the marker then skips).
    from tests import fake_pyocd

    return fake_pyocd


def test_maps_probe_fields_to_probeinfo():
    fp = _fakes()
    api = fp.make_fake_api(
        probes=[
            fp.FakeDebugProbe(
                unique_id="ABC12345678",
                vendor_name="STMicroelectronics",
                product_name="STM32 STLink",
            )
        ]
    )
    probes = discover_pyocd_probes(api)
    assert len(probes) == 1
    probe = probes[0]
    assert probe.backend == "pyocd"
    assert probe.identifier == "ABC12345678"
    assert probe.serial == "ABC12345678"
    assert "STMicroelectronics" in probe.display_name
    assert "STM32 STLink" in probe.display_name
    assert "12345678" in probe.display_name  # last 8 chars of the unique id


def test_multiple_probes_sorted_by_identifier():
    fp = _fakes()
    api = fp.make_fake_api(
        probes=[fp.FakeDebugProbe(unique_id="ZZZ"), fp.FakeDebugProbe(unique_id="AAA")]
    )
    assert [p.identifier for p in discover_pyocd_probes(api)] == ["AAA", "ZZZ"]


def test_probe_without_unique_id_skipped():
    fp = _fakes()
    api = fp.make_fake_api(probes=[fp.FakeDebugProbe(unique_id="")])
    assert discover_pyocd_probes(api) == []


def test_empty_when_no_probes():
    fp = _fakes()
    assert discover_pyocd_probes(fp.make_fake_api(probes=[])) == []


def test_returns_empty_when_pyocd_absent(monkeypatch):
    # No api passed + pyocd "absent" -> [] without ever calling PyocdApi.real().
    monkeypatch.setattr(discovery, "pyocd_available", lambda: False)
    assert discover_pyocd_probes() == []
