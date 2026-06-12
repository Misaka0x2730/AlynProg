"""The central memory hex view widget: region picker, width switch, goto, save, and the table."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QFontDatabase, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from alynprog.core.backend import MemoryRegion, RegionKind
from alynprog.core.session import SessionController
from alynprog.core.settings import Settings
from alynprog.ui.panels.hexview.ascii_delegate import AsciiHighlightDelegate
from alynprog.ui.panels.hexview.model import BYTES_PER_ROW, HexTableModel

_WIDTHS = [("8-bit", 1), ("16-bit", 2), ("32-bit", 4)]


class HexView(QWidget):
    logMessage = Signal(str, str)  # (level, text)

    def __init__(
        self,
        session: SessionController,
        settings: Settings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session = session
        self._settings = settings
        self._recent_goto_mem: list[str] = []  # fallback recents when no Settings is provided
        self._model = HexTableModel(self)
        self._model.set_request_callback(self._session.read_page)
        self._model.set_write_callback(self._on_edit_write)

        self._build_ui()
        self._wire_session()

    def _build_ui(self) -> None:
        self._region_box = QComboBox(self)
        self._region_box.currentIndexChanged.connect(self._on_region_changed)

        # Editable combo: type an address, or pick a recently-visited one from the dropdown.
        self._goto = QComboBox(self)
        self._goto.setEditable(True)
        self._goto.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        # Give "Go to" a roomier field; the stretched Region combo gives up the width.
        self._goto.setMinimumWidth(220)
        self._goto.lineEdit().setPlaceholderText(self.tr("address (0x…)"))
        self._goto.lineEdit().returnPressed.connect(self._on_goto)
        self._goto.activated.connect(lambda _index: self._on_goto())
        self._reload_goto_items()

        self._width_box = QComboBox(self)
        for label, width in _WIDTHS:
            self._width_box.addItem(label, width)
        self._width_box.currentIndexChanged.connect(self._on_width_changed)

        refresh = QPushButton(self.tr("Refresh"), self)
        refresh.clicked.connect(lambda: self._model.invalidate())
        save = QPushButton(self.tr("Save region…"), self)
        save.clicked.connect(self._on_save)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel(self.tr("Region:")))
        toolbar.addWidget(self._region_box, 1)
        toolbar.addWidget(QLabel(self.tr("Go to:")))
        toolbar.addWidget(self._goto)
        toolbar.addWidget(self._width_box)
        toolbar.addWidget(refresh)
        toolbar.addWidget(save)

        self._table = QTableView(self)
        self._table.setModel(self._model)
        # A guaranteed fixed-width font keeps hex/ASCII columns aligned across platforms.
        self._table.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self._table.verticalHeader().setDefaultSectionSize(22)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.setItemDelegate(AsciiHighlightDelegate(self._model, self._table))
        self._table.selectionModel().currentChanged.connect(self._on_current_changed)

        layout = QVBoxLayout(self)
        layout.addLayout(toolbar)
        layout.addWidget(self._table)

    def _wire_session(self) -> None:
        self._session.memory_map_changed.connect(self._on_memory_map)
        self._session.memory_page_ready.connect(self._model.fill_page)
        self._session.memory_written.connect(self._on_write_done)
        # Programming and erasing change device memory; drop stale cached pages.
        self._session.program_done.connect(lambda _result: self._model.invalidate())

    # --- session reactions -----------------------------------------------------

    def _on_memory_map(self, regions: list[MemoryRegion]) -> None:
        self._model.set_memory_map(regions)
        self._region_box.blockSignals(True)
        self._region_box.clear()
        for region in regions:
            name = region.name or region.kind.value
            self._region_box.addItem(f"{name}  0x{region.start:08X}-0x{region.end:08X}", region)
        self._region_box.blockSignals(False)
        if regions:
            self._region_box.setCurrentIndex(0)
            self._on_region_changed(0)
        else:
            self._model.set_region(None)  # clear the table when there is no target/memory map

    def _on_region_changed(self, _index: int) -> None:
        region = self._region_box.currentData()
        self._model.set_region(region)
        self._resize_columns()

    def _on_width_changed(self, _index: int) -> None:
        self._model.set_width(self._width_box.currentData())
        self._resize_columns()

    def _on_current_changed(self, current, _previous) -> None:
        # Mirror the selected hex cell's byte(s) onto the ASCII column highlight.
        ascii_col = self._model.ascii_column()
        if not current.isValid() or current.column() >= ascii_col:
            self._model.set_ascii_highlight(-1, 0, 0)
            return
        lo = current.column() * self._model.width
        hi = lo + self._model.width
        self._model.set_ascii_highlight(current.row(), lo, hi)

    def _on_goto(self) -> None:
        text = self._goto.currentText().strip()
        region = self._model.region
        if not text or region is None:
            return
        try:
            addr = int(text, 16)  # input is interpreted as hex (a leading 0x is accepted)
        except ValueError:
            self.logMessage.emit("warn", self.tr("Invalid address: %s") % text)
            return
        if not region.contains(addr):
            self.logMessage.emit(
                "warn", self.tr("Address 0x%X is outside the selected region") % addr
            )
            return
        row = (addr - region.start) // BYTES_PER_ROW
        self._table.scrollTo(self._model.index(row, 0), QAbstractItemView.ScrollHint.PositionAtTop)
        self._table.selectRow(row)
        self._push_recent_goto(f"0x{addr:08X}")

    def _recent_goto(self) -> list[str]:
        if self._settings is not None:
            return self._settings.recent_goto_addresses
        return self._recent_goto_mem

    def _push_recent_goto(self, address: str) -> None:
        if self._settings is not None:
            self._settings.push_recent_goto(address)
        else:
            items = [a for a in self._recent_goto_mem if a != address]
            items.insert(0, address)
            del items[5:]
            self._recent_goto_mem = items
        self._reload_goto_items()

    def _reload_goto_items(self) -> None:
        # Repopulate the recents dropdown without disturbing the user's current edit text.
        current = self._goto.currentText()
        self._goto.blockSignals(True)
        self._goto.clear()
        self._goto.addItems(self._recent_goto())
        self._goto.setEditText(current)
        self._goto.blockSignals(False)

    def _on_save(self) -> None:
        region = self._model.region
        if region is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Save region to file"), "region.bin", self.tr("Binary (*.bin)")
        )
        if path:
            self._session.save_range(region.start, region.size, path)
            self.logMessage.emit("info", self.tr("Saving region to %s…") % path)

    def _on_edit_write(self, addr: int, data: bytes) -> None:
        self._session.write_bytes(addr, data)

    def _on_write_done(self, addr: int, length: int, ok: bool, error: str) -> None:
        if ok:
            self._model.invalidate(addr, length)
            self.logMessage.emit("info", self.tr("Wrote %d byte(s) at 0x%08X") % (length, addr))
        else:
            self._model.invalidate(addr, length)
            self.logMessage.emit(
                "error", self.tr("Write at 0x%08X failed: %s") % (addr, error or "unknown")
            )

    # --- helpers ---------------------------------------------------------------

    def _resize_columns(self) -> None:
        # Size from the actual font so every hex/ASCII glyph fits (no elision that would shift the
        # ASCII highlight).
        fm = QFontMetrics(self._table.font())
        char_w = fm.horizontalAdvance("0")
        padding = char_w * 2
        hex_cols = BYTES_PER_ROW // self._model.width
        hex_cell = char_w * (self._model.width * 2) + padding
        for col in range(hex_cols):
            self._table.setColumnWidth(col, hex_cell)
        self._table.setColumnWidth(hex_cols, char_w * BYTES_PER_ROW + padding)  # ASCII column

    def refresh(self) -> None:
        """Drop cached pages and re-read the current region from the device."""
        self._model.invalidate()

    def current_region_kind(self, addr: int) -> RegionKind:
        return self._model.region_kind(addr)
