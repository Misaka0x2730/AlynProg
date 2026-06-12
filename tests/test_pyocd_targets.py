"""M2: pyOCD target catalog — record parsing, disk cache round-trip, staleness triggers.

These tests are hardware- and pyocd-free: they drive :class:`TargetCatalog` with a captured
``list_targets()`` snapshot (``tests/data/pyocd_targets_sample.json``) and a stub API.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alynprog.backends.pyocd import targets as targets_mod
from alynprog.backends.pyocd.targets import TargetCatalog, TargetRecord

SAMPLE_PATH = Path(__file__).parent / "data" / "pyocd_targets_sample.json"


@pytest.fixture
def sample_result() -> dict:
    return json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))


class _StubApi:
    """Minimal stand-in exposing only what TargetCatalog.fetch calls."""

    def __init__(self, result: dict) -> None:
        self._result = result
        self.calls = 0

    def list_targets(self, **_kwargs) -> dict:
        self.calls += 1
        return self._result


# --- record parsing ----------------------------------------------------------


def test_from_pyocd_parses_pack_entry():
    rec = TargetRecord.from_pyocd(
        {
            "name": "stm32f030c6",
            "vendor": "STMicroelectronics",
            "part_number": "STM32F030C6",
            "part_families": ["STM32F0 Series", "STM32F030"],
            "source": "pack",
        }
    )
    assert rec.name == "stm32f030c6"
    assert rec.vendor == "STMicroelectronics"
    assert rec.families == ("STM32F0 Series", "STM32F030")
    assert rec.source == "pack"


def test_from_pyocd_handles_empty_families():
    rec = TargetRecord.from_pyocd(
        {"name": "mps2_an521", "vendor": "Arm", "part_number": "AN521", "part_families": []}
    )
    assert rec.families == ()


def test_from_pyocd_tolerates_missing_keys():
    rec = TargetRecord.from_pyocd({"name": "x"})
    assert rec == TargetRecord("x", "", "", (), "")


def test_sample_fixture_has_builtin_and_pack(sample_result):
    sources = {t["source"] for t in sample_result["targets"]}
    assert {"builtin", "pack"} <= sources
    assert len(sample_result["targets"]) > 300


# --- cache round-trip --------------------------------------------------------


def test_fetch_writes_cache_and_returns_records(tmp_path, sample_result):
    catalog = TargetCatalog(tmp_path / "cache.json")
    api = _StubApi(sample_result)

    records = catalog.fetch(api, pack_data_path=None)

    assert api.calls == 1
    assert len(records) == len(sample_result["targets"])
    assert catalog.cache_path.is_file()


def test_load_cached_round_trips_records(tmp_path, sample_result):
    catalog = TargetCatalog(tmp_path / "cache.json")
    fetched = catalog.fetch(_StubApi(sample_result), pack_data_path=None)

    loaded = catalog.load_cached()

    assert loaded == fetched
    # Spot-check a pack record survived the round-trip with its families intact.
    f030 = next(r for r in loaded if r.name == "stm32f030c6")
    assert f030.source == "pack"
    assert "STM32F0 Series" in f030.families


def test_load_cached_missing_returns_none(tmp_path):
    assert TargetCatalog(tmp_path / "nope.json").load_cached() is None


def test_load_cached_corrupt_returns_none(tmp_path):
    cache = tmp_path / "cache.json"
    cache.write_text("{not valid json", encoding="utf-8")
    assert TargetCatalog(cache).load_cached() is None


# --- staleness ---------------------------------------------------------------


def test_missing_cache_is_stale(tmp_path):
    assert TargetCatalog(tmp_path / "nope.json").is_stale() is True


def test_fresh_cache_is_not_stale(tmp_path, sample_result):
    catalog = TargetCatalog(tmp_path / "cache.json")
    catalog.fetch(_StubApi(sample_result), pack_data_path=None)
    assert catalog.is_stale() is False


def test_schema_change_is_stale(tmp_path, sample_result, monkeypatch):
    catalog = TargetCatalog(tmp_path / "cache.json")
    catalog.fetch(_StubApi(sample_result), pack_data_path=None)
    monkeypatch.setattr(targets_mod, "_CACHE_SCHEMA", targets_mod._CACHE_SCHEMA + 1)
    assert catalog.is_stale() is True


def test_pyocd_version_change_is_stale(tmp_path, sample_result, monkeypatch):
    catalog = TargetCatalog(tmp_path / "cache.json")
    monkeypatch.setattr(targets_mod, "_installed_pyocd_version", lambda: "0.44.1")
    catalog.fetch(_StubApi(sample_result), pack_data_path=None)
    assert catalog.is_stale() is False

    monkeypatch.setattr(targets_mod, "_installed_pyocd_version", lambda: "0.45.0")
    assert catalog.is_stale() is True


def test_pack_fingerprint_change_is_stale(tmp_path, sample_result):
    pack_dir = tmp_path / "packs"
    pack_dir.mkdir()
    (pack_dir / "index.json").write_text("{}", encoding="utf-8")
    vendor_dir = pack_dir / "Keil" / "STM32F0xx_DFP"
    vendor_dir.mkdir(parents=True)
    pack_file = vendor_dir / "3.1.1.pack"
    pack_file.write_bytes(b"original")

    catalog = TargetCatalog(tmp_path / "cache.json")
    catalog.fetch(_StubApi(sample_result), pack_data_path=pack_dir)
    assert catalog.is_stale() is False

    # Simulate `pyocd pack install/update`: a pack file changes size.
    pack_file.write_bytes(b"a different, longer payload")
    assert catalog.is_stale() is True


def test_new_pack_file_is_stale(tmp_path, sample_result):
    pack_dir = tmp_path / "packs"
    pack_dir.mkdir()
    (pack_dir / "index.json").write_text("{}", encoding="utf-8")

    catalog = TargetCatalog(tmp_path / "cache.json")
    catalog.fetch(_StubApi(sample_result), pack_data_path=pack_dir)
    assert catalog.is_stale() is False

    (pack_dir / "NewVendor").mkdir()
    (pack_dir / "NewVendor" / "1.0.0.pack").write_bytes(b"new pack")
    assert catalog.is_stale() is True


def test_no_pack_path_falls_back_to_version_only(tmp_path, sample_result, monkeypatch):
    # When pack dir can't be resolved, fetch stores no fingerprint and is_stale relies on version.
    monkeypatch.setattr(targets_mod, "_resolve_pack_data_path", lambda: None)
    catalog = TargetCatalog(tmp_path / "cache.json")
    catalog.fetch(_StubApi(sample_result))  # pack_data_path defaults to None -> resolver -> None
    data = json.loads(catalog.cache_path.read_text())
    assert data["pack_fingerprint"] is None
    assert catalog.is_stale() is False
