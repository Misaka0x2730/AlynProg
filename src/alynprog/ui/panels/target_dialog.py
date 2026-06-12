"""Target-selection dialog for the pyOCD backend.

pyOCD cannot auto-detect the connected MCU, so the user picks one from pyOCD's own target database
(never a hardcoded list) via a searchable table — filterable by free text (name / part number),
by vendor, and by family. The catalog is loaded from :class:`TargetCatalog`; a cold fetch runs on a
background thread so the UI stays responsive.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import (
    QAbstractTableModel,
    QMetaObject,
    QModelIndex,
    QObject,
    QSortFilterProxyModel,
    Qt,
    QThread,
    Signal,
    Slot,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from alynprog.backends.pyocd._api import PyocdApi
from alynprog.backends.pyocd.targets import TargetCatalog, TargetRecord
from alynprog.core.settings import Settings

# Role on column 0 that returns the whole TargetRecord for a row.
TARGET_ROLE = Qt.ItemDataRole.UserRole

# Untranslated column keys; headers are translated in headerData.
_COLUMN_TITLES = ("Name", "Vendor", "Part Number", "Families", "Source")


class TargetTableModel(QAbstractTableModel):
    """Flat table over a list of :class:`TargetRecord`."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: list[TargetRecord] = []

    def set_records(self, records: list[TargetRecord]) -> None:
        self.beginResetModel()
        self._rows = list(records)
        self.endResetModel()

    def record_at(self, row: int) -> TargetRecord | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(_COLUMN_TITLES)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        record = self._rows[index.row()]
        if role == TARGET_ROLE:
            return record
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                record.name,
                record.vendor,
                record.part_number,
                ", ".join(record.families),
                record.source,
            )[index.column()]
        return None

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ):
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        # Literal tr() calls so the header strings are extracted by pyside6-lupdate.
        titles = (
            self.tr("Name"),
            self.tr("Vendor"),
            self.tr("Part Number"),
            self.tr("Families"),
            self.tr("Source"),
        )
        return titles[section]


class TargetFilterProxyModel(QSortFilterProxyModel):
    """Filters target rows by free text (name/part number) AND vendor AND family."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._text = ""
        self._vendor = ""  # "" means "all vendors"
        self._family = ""  # "" means "all families"

    def set_text_filter(self, text: str) -> None:
        self._text = text.strip().lower()
        self.invalidate()

    def set_vendor_filter(self, vendor: str) -> None:
        self._vendor = vendor
        self.invalidate()

    def set_family_filter(self, family: str) -> None:
        self._family = family
        self.invalidate()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if not isinstance(model, TargetTableModel):
            return True
        record = model.record_at(source_row)
        if record is None:
            return False
        if self._vendor and record.vendor != self._vendor:
            return False
        if self._family and self._family not in record.families:
            return False
        if self._text:
            haystack = f"{record.name}\n{record.part_number}".lower()
            if self._text not in haystack:
                return False
        return True


class _CatalogLoader(QObject):
    """Runs the (slow) catalog fetch on a worker thread and reports back via signals."""

    loaded = Signal(object)  # list[TargetRecord]
    failed = Signal(str)

    def __init__(self, catalog: TargetCatalog, api_provider: Callable[[], PyocdApi]) -> None:
        super().__init__()
        self._catalog = catalog
        self._api_provider = api_provider

    @Slot()
    def run(self) -> None:
        try:
            records = self._catalog.fetch(self._api_provider())
        except Exception as exc:  # surface any failure to the dialog as text
            self.failed.emit(str(exc))
        else:
            self.loaded.emit(records)


class TargetSelectDialog(QDialog):
    """Modal picker returning the chosen pyOCD target (its ``name``)."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        settings: Settings,
        probe_serial: str = "",
        catalog: TargetCatalog,
        api_provider: Callable[[], PyocdApi] = PyocdApi.real,
        auto_load: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Select Target"))
        self._settings = settings
        self._probe_serial = probe_serial
        self._catalog = catalog
        self._api_provider = api_provider
        self._records: list[TargetRecord] = []
        self._selected: TargetRecord | None = None
        self._thread: QThread | None = None
        self._loader: _CatalogLoader | None = None

        self._model = TargetTableModel(self)
        self._proxy = TargetFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)

        self._build_ui()
        self.finished.connect(lambda _result: self._teardown())

        if auto_load:
            self._initial_load()

    # --- UI construction -------------------------------------------------------

    def _build_ui(self) -> None:
        self._search = QLineEdit(self)
        self._search.setPlaceholderText(self.tr("Search name or part number…"))
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._proxy.set_text_filter)

        self._vendor_box = QComboBox(self)
        self._vendor_box.currentIndexChanged.connect(self._on_vendor_changed)
        self._family_box = QComboBox(self)
        self._family_box.currentIndexChanged.connect(self._on_family_changed)

        filters = QHBoxLayout()
        filters.addWidget(self._search, 1)
        filters.addWidget(self._vendor_box)
        filters.addWidget(self._family_box)

        self._table = QTableView(self)
        self._table.setModel(self._proxy)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSortingEnabled(True)
        self._table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        # Name (the target id you pick) must always be fully visible; Families takes the slack and
        # elides if narrow. The rest size to their content.
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # Name
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # Vendor
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # Part Number
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)  # Families
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)  # Source
        self._table.doubleClicked.connect(self._on_double_clicked)
        self._table.selectionModel().currentRowChanged.connect(self._on_current_row_changed)

        self._loading = QLabel(self.tr("Loading target list…"), self)
        self._loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading.hide()

        self._status = QLabel(self)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self._refresh_btn = self._buttons.addButton(
            self.tr("Refresh"), QDialogButtonBox.ButtonRole.ResetRole
        )
        self._refresh_btn.clicked.connect(self._on_refresh)
        self._buttons.accepted.connect(self._accept)
        self._buttons.rejected.connect(self.reject)
        self._ok_button().setEnabled(False)

        bottom = QHBoxLayout()
        bottom.addWidget(self._status, 1)
        bottom.addWidget(self._buttons)

        layout = QVBoxLayout(self)
        layout.addLayout(filters)
        layout.addWidget(self._table, 1)
        layout.addWidget(self._loading)
        layout.addLayout(bottom)
        self.resize(760, 480)

    def _ok_button(self):
        return self._buttons.button(QDialogButtonBox.StandardButton.Ok)

    # --- loading ---------------------------------------------------------------

    def _initial_load(self) -> None:
        cached = self._catalog.load_cached()
        if cached is None:
            self._start_fetch()
            return
        self._populate(cached)
        if self._catalog.is_stale():
            self._start_fetch(background=True)

    def _ensure_thread(self) -> None:
        if self._thread is not None:
            return
        self._thread = QThread(self)
        self._loader = _CatalogLoader(self._catalog, self._api_provider)
        self._loader.moveToThread(self._thread)
        self._loader.loaded.connect(self._on_loaded)
        self._loader.failed.connect(self._on_failed)
        self._thread.start()

    def _start_fetch(self, *, background: bool = False) -> None:
        self._ensure_thread()
        if not background:
            self._set_loading(True)
        self._refresh_btn.setEnabled(False)
        QMetaObject.invokeMethod(self._loader, "run", Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _on_loaded(self, records: list[TargetRecord]) -> None:
        self._set_loading(False)
        self._refresh_btn.setEnabled(True)
        self._populate(records)

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._set_loading(False)
        self._refresh_btn.setEnabled(True)
        self._status.setText(self.tr("Failed to load targets: {error}").format(error=message))

    def _set_loading(self, loading: bool) -> None:
        self._loading.setVisible(loading)
        self._table.setVisible(not loading)

    def _teardown(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            if self._thread.wait(5000):
                self._thread = None
                self._loader = None
            # else: a fetch is still in flight; keep the refs so the live QThread is not
            # destroyed mid-run (it finishes on its own shortly after).

    # --- population & filters --------------------------------------------------

    def _populate(self, records: list[TargetRecord]) -> None:
        self._records = list(records)
        self._model.set_records(self._records)
        self._rebuild_vendor_box()
        self._rebuild_family_box("")
        self._update_status()
        self._preselect_remembered()

    def _rebuild_vendor_box(self) -> None:
        vendors = sorted({r.vendor for r in self._records if r.vendor})
        self._vendor_box.blockSignals(True)
        self._vendor_box.clear()
        self._vendor_box.addItem(self.tr("All vendors"), "")
        for vendor in vendors:
            self._vendor_box.addItem(vendor, vendor)
        self._vendor_box.setCurrentIndex(0)
        self._vendor_box.blockSignals(False)
        # Reset to "All vendors" silently, so clear the proxy's vendor filter to match.
        self._proxy.set_vendor_filter("")

    def _rebuild_family_box(self, vendor: str) -> None:
        families = sorted(
            {
                family
                for r in self._records
                if (not vendor or r.vendor == vendor)
                for family in r.families
            }
        )
        self._family_box.blockSignals(True)
        self._family_box.clear()
        self._family_box.addItem(self.tr("All families"), "")
        for family in families:
            self._family_box.addItem(family, family)
        self._family_box.setCurrentIndex(0)
        self._family_box.blockSignals(False)
        self._proxy.set_family_filter("")

    def _on_vendor_changed(self) -> None:
        vendor = self._vendor_box.currentData() or ""
        self._proxy.set_vendor_filter(vendor)
        self._rebuild_family_box(vendor)

    def _on_family_changed(self) -> None:
        self._proxy.set_family_filter(self._family_box.currentData() or "")

    def _update_status(self) -> None:
        total = len(self._records)
        builtin = sum(1 for r in self._records if r.source == "builtin")
        pack = sum(1 for r in self._records if r.source == "pack")
        self._status.setText(
            self.tr("{total} targets ({builtin} builtin, {pack} from packs)").format(
                total=total, builtin=builtin, pack=pack
            )
        )

    # --- selection -------------------------------------------------------------

    def _record_for_proxy_row(self, proxy_index: QModelIndex) -> TargetRecord | None:
        if not proxy_index.isValid():
            return None
        source_index = self._proxy.mapToSource(proxy_index)
        return self._model.record_at(source_index.row())

    def _on_current_row_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        self._selected = self._record_for_proxy_row(current)
        self._ok_button().setEnabled(self._selected is not None)

    def _preselect_remembered(self) -> None:
        remembered = self._settings.pyocd_last_target(self._probe_serial)
        if not remembered:
            return
        for row in range(self._model.rowCount()):
            if self._model.record_at(row).name == remembered:
                proxy_index = self._proxy.mapFromSource(self._model.index(row, 0))
                if proxy_index.isValid():
                    self._table.setCurrentIndex(proxy_index)
                    self._table.scrollTo(proxy_index)
                return

    def _on_double_clicked(self, proxy_index: QModelIndex) -> None:
        record = self._record_for_proxy_row(proxy_index)
        if record is not None:
            self._selected = record
            self._accept()

    def _on_refresh(self) -> None:
        self._start_fetch()

    def _accept(self) -> None:
        if self._selected is None:
            return
        self._settings.set_pyocd_last_target(self._probe_serial, self._selected.name)
        self.accept()

    def selected_target(self) -> TargetRecord | None:
        """The chosen target, or ``None`` if the dialog was cancelled without a selection."""
        return self._selected if self.result() == QDialog.DialogCode.Accepted else None
