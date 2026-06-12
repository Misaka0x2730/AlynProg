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
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from alynprog.core.backend import MemoryRegion, RegionKind
from alynprog.core.session import SessionController
from alynprog.ui.panels.hexview.ascii_delegate import AsciiHighlightDelegate
from alynprog.ui.panels.hexview.model import BYTES_PER_ROW, HexTableModel

_WIDTHS = [("8-bit", 1), ("16-bit", 2), ("32-bit", 4)]


class HexView(QWidget):
    logMessage = Signal(str, str)  # (level, text)

    def __init__(self, session: SessionController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session = session
        self._model = HexTableModel(self)
        self._model.set_request_callback(self._session.read_page)
        self._model.set_write_callback(self._on_edit_write)

        self._build_ui()
        self._wire_session()

    def _build_ui(self) -> None:
        self._region_box = QComboBox(self)
        self._region_box.currentIndexChanged.connect(self._on_region_changed)

        self._goto = QLineEdit(self)
        self._goto.setPlaceholderText(self.tr("address (0x…)"))
        self._goto.setMaximumWidth(140)
        self._goto.returnPressed.connect(self._on_goto)

        self._width_box = QComboBox(self)
        for label, width in _WIDTHS:
            self._width_box.addItem(label, width)
        self._width_box.currentIndexChanged.connect(self._on_width_changed)

        from PySide6.QtWidgets import QCheckBox

        self._edit_unknown = QCheckBox(self.tr("Edit unknown regions"), self)
        self._edit_unknown.toggled.connect(self._on_edit_unknown)

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
        toolbar.addWidget(self._edit_unknown)
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
        text = self._goto.text().strip()
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

    def _on_edit_unknown(self, checked: bool) -> None:
        if checked and not self._confirm_unknown_edit():
            self._edit_unknown.setChecked(False)
            return
        self._model.set_allow_unknown_edit(checked)

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

    def _confirm_unknown_edit(self) -> bool:
        from PySide6.QtWidgets import QMessageBox

        answer = QMessageBox.warning(
            self,
            self.tr("Edit unknown regions"),
            self.tr(
                "Unknown regions are not classified as RAM or flash. Writing to them may fail or "
                "have unexpected effects. Enable editing anyway?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

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
