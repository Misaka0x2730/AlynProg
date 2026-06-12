"""M3: target-selection dialog — model, proxy filtering, selection, persistence (offscreen)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog

from alynprog.backends.pyocd import targets as targets_mod
from alynprog.backends.pyocd.targets import TargetCatalog, TargetRecord
from alynprog.ui.panels.target_dialog import TargetSelectDialog, _CatalogLoader

SAMPLE_PATH = Path(__file__).parent / "data" / "pyocd_targets_sample.json"


@pytest.fixture
def sample_result() -> dict:
    return json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def records(sample_result) -> list[TargetRecord]:
    return [TargetRecord.from_pyocd(t) for t in sample_result["targets"]]


class _StubApi:
    def __init__(self, result: dict) -> None:
        self._result = result

    def list_targets(self, **_kwargs) -> dict:
        return self._result


def _make_dialog(qtbot, temp_settings, tmp_path, *, probe_serial="") -> TargetSelectDialog:
    catalog = TargetCatalog(tmp_path / "cache.json")
    dialog = TargetSelectDialog(
        settings=temp_settings,
        probe_serial=probe_serial,
        catalog=catalog,
        auto_load=False,  # tests drive population directly; no worker thread
    )
    qtbot.addWidget(dialog)
    return dialog


def _select_combo_data(box, value: str) -> None:
    box.setCurrentIndex(box.findData(value))


# --- model -------------------------------------------------------------------


def test_model_has_five_columns(qtbot, temp_settings, tmp_path, records):
    dialog = _make_dialog(qtbot, temp_settings, tmp_path)
    dialog._populate(records)
    assert dialog._model.columnCount() == 5
    assert dialog._model.rowCount() == len(records)


def test_name_column_auto_fits_families_stretches(qtbot, temp_settings, tmp_path, records):
    from PySide6.QtWidgets import QHeaderView

    dialog = _make_dialog(qtbot, temp_settings, tmp_path)
    dialog._populate(records)
    header = dialog._table.horizontalHeader()
    # Name auto-fits its content so target ids are visible without manual resizing; the secondary
    # Families column is the flexible one.
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.ResizeToContents
    assert header.sectionResizeMode(3) == QHeaderView.ResizeMode.Stretch


def test_proxy_shows_all_rows_initially(qtbot, temp_settings, tmp_path, records):
    dialog = _make_dialog(qtbot, temp_settings, tmp_path)
    dialog._populate(records)
    assert dialog._proxy.rowCount() == len(records)


# --- filtering ---------------------------------------------------------------


def _visible_records(dialog) -> list[TargetRecord]:
    out = []
    for row in range(dialog._proxy.rowCount()):
        out.append(dialog._record_for_proxy_row(dialog._proxy.index(row, 0)))
    return out


def test_text_filter_matches_name_and_part_number(qtbot, temp_settings, tmp_path, records):
    dialog = _make_dialog(qtbot, temp_settings, tmp_path)
    dialog._populate(records)

    dialog._search.setText("stm32f103")
    visible = _visible_records(dialog)
    assert visible  # at least stm32f103rc
    assert all(
        "stm32f103" in r.name.lower() or "stm32f103" in r.part_number.lower() for r in visible
    )
    assert any(r.name == "stm32f103rc" for r in visible)


def test_text_filter_is_case_insensitive(qtbot, temp_settings, tmp_path, records):
    dialog = _make_dialog(qtbot, temp_settings, tmp_path)
    dialog._populate(records)
    dialog._search.setText("STM32F103RC")
    assert any(r.name == "stm32f103rc" for r in _visible_records(dialog))


def test_vendor_filter(qtbot, temp_settings, tmp_path, records):
    dialog = _make_dialog(qtbot, temp_settings, tmp_path)
    dialog._populate(records)

    _select_combo_data(dialog._vendor_box, "STMicroelectronics")
    visible = _visible_records(dialog)
    assert visible
    assert all(r.vendor == "STMicroelectronics" for r in visible)


def test_vendor_change_repopulates_family_combo(qtbot, temp_settings, tmp_path, records):
    dialog = _make_dialog(qtbot, temp_settings, tmp_path)
    dialog._populate(records)

    _select_combo_data(dialog._vendor_box, "STMicroelectronics")
    families = {dialog._family_box.itemData(i) for i in range(dialog._family_box.count())}
    st_families = {f for r in records if r.vendor == "STMicroelectronics" for f in r.families}
    # All-families sentinel ("") plus exactly that vendor's families.
    assert families == st_families | {""}


def test_family_filter(qtbot, temp_settings, tmp_path, records):
    dialog = _make_dialog(qtbot, temp_settings, tmp_path)
    dialog._populate(records)

    _select_combo_data(dialog._vendor_box, "STMicroelectronics")
    family = next(f for r in records if r.vendor == "STMicroelectronics" for f in r.families)
    _select_combo_data(dialog._family_box, family)

    visible = _visible_records(dialog)
    assert visible
    assert all(family in r.families for r in visible)


# --- selection & gating ------------------------------------------------------


def test_ok_disabled_until_selection(qtbot, temp_settings, tmp_path, records):
    dialog = _make_dialog(qtbot, temp_settings, tmp_path)
    dialog._populate(records)
    assert not dialog._ok_button().isEnabled()

    dialog._table.setCurrentIndex(dialog._proxy.index(0, 0))
    assert dialog._ok_button().isEnabled()


def test_double_click_accepts_with_record(qtbot, temp_settings, tmp_path, records):
    dialog = _make_dialog(qtbot, temp_settings, tmp_path)
    dialog._populate(records)

    first = dialog._record_for_proxy_row(dialog._proxy.index(0, 0))
    dialog._on_double_clicked(dialog._proxy.index(0, 0))

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected_target() == first


def test_cancel_returns_no_target(qtbot, temp_settings, tmp_path, records):
    dialog = _make_dialog(qtbot, temp_settings, tmp_path)
    dialog._populate(records)
    dialog._table.setCurrentIndex(dialog._proxy.index(0, 0))
    dialog.reject()
    assert dialog.selected_target() is None


# --- persistence -------------------------------------------------------------


def test_preselects_remembered_target(qtbot, temp_settings, tmp_path, records):
    temp_settings.set_pyocd_last_target("SN123", "stm32f103rc")
    dialog = _make_dialog(qtbot, temp_settings, tmp_path, probe_serial="SN123")
    dialog._populate(records)
    assert dialog._selected is not None
    assert dialog._selected.name == "stm32f103rc"


def test_accept_persists_selection(qtbot, temp_settings, tmp_path, records):
    dialog = _make_dialog(qtbot, temp_settings, tmp_path, probe_serial="SN999")
    dialog._populate(records)
    dialog._table.setCurrentIndex(dialog._proxy.index(0, 0))
    chosen = dialog._selected
    dialog._accept()
    assert temp_settings.pyocd_last_target("SN999") == chosen.name


# --- auto-load + loader thread ----------------------------------------------


def test_auto_load_populates_from_fresh_cache(
    qtbot, temp_settings, tmp_path, sample_result, monkeypatch
):
    # Seed a non-stale cache, then a default (auto_load) dialog must populate without a fetch.
    monkeypatch.setattr(targets_mod, "_installed_pyocd_version", lambda: "0.44.1")
    catalog = TargetCatalog(tmp_path / "cache.json")
    catalog.fetch(_StubApi(sample_result), pack_data_path=None)
    assert not catalog.is_stale()

    dialog = TargetSelectDialog(
        settings=temp_settings, catalog=catalog, api_provider=_unreachable_api
    )
    qtbot.addWidget(dialog)
    assert dialog._thread is None  # no worker thread spawned on a cache hit
    assert dialog._proxy.rowCount() == len(sample_result["targets"])


def test_stale_cache_is_shown_and_spawns_background_refresh(
    qtbot, temp_settings, tmp_path, sample_result, monkeypatch
):
    # Seed a cache, then make it stale (different pyocd version).
    monkeypatch.setattr(targets_mod, "_installed_pyocd_version", lambda: "0.44.1")
    catalog = TargetCatalog(tmp_path / "cache.json")
    catalog.fetch(_StubApi(sample_result), pack_data_path=None)
    monkeypatch.setattr(targets_mod, "_installed_pyocd_version", lambda: "0.45.0")
    assert catalog.is_stale()

    dialog = TargetSelectDialog(
        settings=temp_settings, catalog=catalog, api_provider=lambda: _StubApi(sample_result)
    )
    qtbot.addWidget(dialog)
    try:
        # The stale cache is shown immediately, and a background refresh thread is spawned.
        assert dialog._proxy.rowCount() == len(sample_result["targets"])
        assert dialog._thread is not None
    finally:
        dialog._teardown()
    assert dialog._thread is None  # background loader stopped cleanly


def _unreachable_api():
    raise AssertionError("api_provider must not be called on a fresh-cache hit")


def test_loader_emits_loaded_on_success(qtbot, tmp_path, sample_result):
    catalog = TargetCatalog(tmp_path / "cache.json")
    loader = _CatalogLoader(catalog, lambda: _StubApi(sample_result))
    with qtbot.waitSignal(loader.loaded, timeout=2000) as blocker:
        loader.run()
    assert len(blocker.args[0]) == len(sample_result["targets"])
    assert catalog.cache_path.is_file()


def test_loader_emits_failed_on_error(qtbot, tmp_path):
    class _BoomApi:
        def list_targets(self, **_kwargs):
            raise RuntimeError("kaboom")

    catalog = TargetCatalog(tmp_path / "cache.json")
    loader = _CatalogLoader(catalog, lambda: _BoomApi())
    with qtbot.waitSignal(loader.failed, timeout=2000) as blocker:
        loader.run()
    assert "kaboom" in blocker.args[0]
