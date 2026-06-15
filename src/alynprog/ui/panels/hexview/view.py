"""The hex view: a reusable pane plus the live device-memory view built on top of it.

:class:`HexPane` is the source-agnostic widget — a toolbar (region picker, go-to, cell width) over
a :class:`HexTableModel` table. A subclass binds a data source (device session or file document),
feeds it regions, and contributes its own toolbar buttons. :class:`HexView` is the device-memory
subclass; the file-document tab reuses :class:`HexPane` the same way.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFontMetrics
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
from alynprog.ui.panels.hexview.font import (
    HexFontController,
    clamp_point_size,
    default_point_size,
    fixed_font,
)
from alynprog.ui.panels.hexview.model import BYTES_PER_ROW, HexTableModel

_WIDTHS = [("8-bit", 1), ("16-bit", 2), ("32-bit", 4)]


class HexPane(QWidget):
    """Toolbar (region / go-to / width) plus a hex table over a :class:`HexTableModel`.

    Subclasses bind a data source with :meth:`_bind_source`, push regions in via
    :meth:`_populate_regions`, and add trailing toolbar buttons by overriding
    :meth:`_toolbar_extras`.
    """

    logMessage = Signal(str, str)  # (level, text)

    def __init__(
        self,
        settings: Settings | None = None,
        font_controller: HexFontController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._recent_goto_mem: list[str] = []  # fallback recents when no Settings is provided
        self._model = HexTableModel(self)
        self._font_controller = font_controller
        self._point_size = self._initial_point_size()
        self._build_ui()
        if font_controller is not None:
            # Re-font live when the shared size changes (status-bar slider, zoom actions, Ctrl+wheel
            # in another pane). Each open pane listens independently so they all stay in lockstep.
            font_controller.sizeChanged.connect(self._apply_font_point_size)

    def _initial_point_size(self) -> int:
        if self._font_controller is not None:
            return self._font_controller.point_size
        if self._settings is not None and self._settings.hex_font_point_size > 0:
            # Clamp here too: a controller always clamps, but a controller-less pane reads the raw
            # persisted value directly, so guard against a stale/out-of-range setting.
            return clamp_point_size(self._settings.hex_font_point_size)
        return default_point_size()

    # --- construction ----------------------------------------------------------

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

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel(self.tr("Region:")))
        toolbar.addWidget(self._region_box, 1)
        toolbar.addWidget(QLabel(self.tr("Go to:")))
        toolbar.addWidget(self._goto)
        toolbar.addWidget(self._width_box)
        for widget in self._toolbar_extras():
            toolbar.addWidget(widget)

        self._table = QTableView(self)
        self._table.setModel(self._model)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.setItemDelegate(AsciiHighlightDelegate(self._model, self._table))
        self._table.selectionModel().currentChanged.connect(self._on_current_changed)
        # The fixed-width font (kept aligned across platforms) and the matching row height are both
        # driven by the user-adjustable point size; Ctrl+wheel over the table zooms it too.
        self._apply_font_point_size(self._point_size)
        self._table.viewport().installEventFilter(self)

        self._layout = QVBoxLayout(self)
        self._layout.addLayout(toolbar)
        self._layout.addWidget(self._table)

    def _toolbar_extras(self) -> list[QWidget]:
        """Trailing toolbar widgets; overridden by subclasses. Called once during construction."""
        return []

    # --- source binding & regions ----------------------------------------------

    def _bind_source(self, request, write) -> None:
        self._model.set_request_callback(request)
        self._model.set_write_callback(write)

    def _populate_regions(self, regions: list[MemoryRegion]) -> None:
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
            self._model.set_region(None)  # clear the table when there are no regions

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

    # --- go to -----------------------------------------------------------------

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
        self._scroll_to_address(addr)
        self._push_recent_goto(f"0x{addr:08X}")

    def _scroll_to_address(self, addr: int) -> bool:
        """Scroll the table so *addr* is the top row and select it. Returns False if off-region."""
        region = self._model.region
        if region is None or not region.contains(addr):
            return False
        row = (addr - region.start) // BYTES_PER_ROW
        self._table.scrollTo(self._model.index(row, 0), QAbstractItemView.ScrollHint.PositionAtTop)
        self._table.selectRow(row)
        return True

    def goto_address(self, addr: int) -> bool:
        """Select the region containing *addr* (if needed) and scroll to it."""
        for i in range(self._region_box.count()):
            region = self._region_box.itemData(i)
            if region is not None and region.contains(addr):
                if self._region_box.currentIndex() != i:
                    self._region_box.setCurrentIndex(i)
                return self._scroll_to_address(addr)
        return False

    def sync_with(self, other: HexPane) -> None:
        """Keep this pane's scroll position, region and cell width locked to *other* (both ways).

        Qt only re-emits ``valueChanged`` / ``currentIndexChanged`` when the value actually changes,
        so the bidirectional connections converge instead of looping.
        """
        here, there = self._table.verticalScrollBar(), other._table.verticalScrollBar()
        here.valueChanged.connect(there.setValue)
        there.valueChanged.connect(here.setValue)
        self._region_box.currentIndexChanged.connect(other._region_box.setCurrentIndex)
        other._region_box.currentIndexChanged.connect(self._region_box.setCurrentIndex)
        self._width_box.currentIndexChanged.connect(other._width_box.setCurrentIndex)
        other._width_box.currentIndexChanged.connect(self._width_box.setCurrentIndex)

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

    # --- font size -------------------------------------------------------------

    def _apply_font_point_size(self, point_size: int) -> None:
        """Re-font the table at *point_size* and re-derive the row height and column widths."""
        self._point_size = point_size
        font = fixed_font(point_size)
        self._table.setFont(font)
        # Grow the row height with the font so taller glyphs are not clipped (a little padding keeps
        # the selection highlight off the cell borders).
        self._table.verticalHeader().setDefaultSectionSize(QFontMetrics(font).height() + 6)
        self._resize_columns()

    def _zoom(self, delta: int) -> None:
        """Step the font size by *delta* points — shared via the controller when one is wired."""
        if self._font_controller is not None:
            self._font_controller.step(delta)
        else:
            self._apply_font_point_size(clamp_point_size(self._point_size + delta))

    def eventFilter(self, watched, event) -> bool:
        # Ctrl+wheel over the table zooms the content, the familiar gesture for hex/text viewers.
        if (
            event.type() == QEvent.Type.Wheel
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            delta = event.angleDelta().y()
            if delta:
                self._zoom(1 if delta > 0 else -1)
            return True
        return super().eventFilter(watched, event)

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


class HexView(HexPane):
    """The live device-memory hex view, with Refresh and Save-region."""

    def __init__(
        self,
        session: SessionController,
        settings: Settings | None = None,
        font_controller: HexFontController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(settings, font_controller, parent)
        self._session = session
        self._bind_source(self._session.read_page, self._on_edit_write)
        self._wire_session()

    def _toolbar_extras(self) -> list[QWidget]:
        refresh = QPushButton(self.tr("Refresh"), self)
        refresh.clicked.connect(lambda: self._model.invalidate())
        save = QPushButton(self.tr("Save region…"), self)
        save.clicked.connect(self._on_save)
        return [refresh, save]

    def _wire_session(self) -> None:
        self._session.memory_map_changed.connect(self._on_memory_map)
        self._session.memory_page_ready.connect(self._model.fill_page)
        self._session.memory_written.connect(self._on_write_done)
        # Programming and erasing change device memory; drop stale cached pages.
        self._session.program_done.connect(lambda _result: self._model.invalidate())

    # --- session reactions -----------------------------------------------------

    def _on_memory_map(self, regions: list[MemoryRegion]) -> None:
        self._model.set_memory_map(regions)
        self._populate_regions(regions)

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
        self._model.invalidate(addr, length)
        if ok:
            self.logMessage.emit("info", self.tr("Wrote %d byte(s) at 0x%08X") % (length, addr))
        else:
            self.logMessage.emit(
                "error", self.tr("Write at 0x%08X failed: %s") % (addr, error or "unknown")
            )

    def refresh(self) -> None:
        """Drop cached pages and re-read the current region from the device."""
        self._model.invalidate()

    def current_region_kind(self, addr: int) -> RegionKind:
        return self._model.region_kind(addr)
