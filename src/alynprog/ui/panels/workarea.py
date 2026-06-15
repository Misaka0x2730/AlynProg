"""The Memory page's tabbed work area: a pinned device-memory tab plus file and compare tabs.

The first tab always holds the live device hex view and cannot be closed; opening a firmware file
adds a closable :class:`FileTab` beside it, and a comparison opens a closable side-by-side
:class:`CompareTab`. The work area keeps file-tab titles in sync with their dirty marker and routes
per-tab log messages up to the window's log pane.

Comparisons are started from the tab context menu: **Compare with…** on a file or the device tab
arms a one-shot pick, and the next click on another tab runs the comparison — file against file
(computed in memory here) or, when either side is the device tab, file against device memory (read
back through the session, the same path the file tab's own Compare button uses). Clicking off the
tabs cancels the pick. A raw ``.bin`` carries no address, so when it is compared against an
addressed file (or device memory) the user is first asked where it starts.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QMenu, QTabBar, QTabWidget, QWidget

from alynprog.core.compare import MemoryCompareResult, compare_buffers
from alynprog.core.document import FirmwareDocument
from alynprog.core.image import ImageKind
from alynprog.core.session import SessionController
from alynprog.core.settings import Settings
from alynprog.ui.panels.base_address_dialog import BaseAddressDialog
from alynprog.ui.panels.compare_tab import CompareTab
from alynprog.ui.panels.file_tab import FileTab
from alynprog.ui.panels.hexview.font import HexFontController
from alynprog.ui.panels.hexview.view import HexView

_DEFAULT_BASE = 0x0800_0000  # fallback start address for a .bin when no settings are available


class MemoryWorkArea(QTabWidget):
    logMessage = Signal(str, str)  # (level, text)
    openRequested = Signal()  # the user asked to open a file (window owns the file dialog)

    def __init__(
        self,
        device_view: HexView,
        session: SessionController,
        settings: Settings | None = None,
        font_controller: HexFontController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session = session
        self._settings = settings
        self._font_controller = font_controller
        # The tab the user armed via "Compare with…"; the next tab click compares against it.
        self._compare_source: QWidget | None = None
        self.setTabsClosable(True)
        self.setMovable(True)
        self.setDocumentMode(True)

        self.addTab(device_view, self.tr("Device memory"))
        self._pin_device_tab()
        self.tabCloseRequested.connect(self._on_close_requested)

        # Right-clicking the tab strip offers Open / Compare with / Close; the filter catches the
        # follow-up click that picks the comparison target.
        bar = self.tabBar()
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        bar.customContextMenuRequested.connect(self._show_tab_menu)
        bar.installEventFilter(self)

    def _pin_device_tab(self) -> None:
        # The device tab is permanent: strip its close button on whichever side the style placed it.
        bar = self.tabBar()
        for side in (QTabBar.ButtonPosition.RightSide, QTabBar.ButtonPosition.LeftSide):
            bar.setTabButton(0, side, None)

    # --- file tabs -------------------------------------------------------------

    def open_document(self, document: FirmwareDocument) -> FileTab:
        tab = FileTab(document, self._session, self._settings, self._font_controller, self)
        tab.logMessage.connect(self.logMessage)
        tab.dirtyChanged.connect(lambda _dirty, t=tab: self._refresh_title(t))
        tab.compareReady.connect(self._on_file_compare_ready)
        index = self.addTab(tab, tab.title())
        self.setTabToolTip(index, str(document.path))
        self.setCurrentIndex(index)
        return tab

    def _on_file_compare_ready(self, result: MemoryCompareResult, file_label: str) -> CompareTab:
        # A file-vs-device compare always reads device memory on the left, the file on the right.
        return self._open_compare_result(result, self.tr("Device memory"), file_label)

    def _open_compare_result(
        self, result: MemoryCompareResult, left_label: str, right_label: str
    ) -> CompareTab:
        tab = CompareTab(
            result, left_label, right_label, self._settings, self._font_controller, self
        )
        index = self.addTab(tab, CompareTab.title_for(left_label, right_label))
        self.setCurrentIndex(index)
        return tab

    def file_tabs(self) -> list[FileTab]:
        return [w for i in range(self.count()) if isinstance(w := self.widget(i), FileTab)]

    def current_file_tab(self) -> FileTab | None:
        widget = self.currentWidget()
        return widget if isinstance(widget, FileTab) else None

    def close_file_tab(self, tab: FileTab) -> bool:
        """Close *tab*, prompting about unsaved edits. Returns False if the user cancelled."""
        if not tab.maybe_save():
            return False
        index = self.indexOf(tab)
        if index >= 0:
            self.removeTab(index)  # cancels any armed compare pick via tabRemoved()
            tab.deleteLater()
        return True

    def tabRemoved(self, index: int) -> None:
        # Any tab going away — the armed source or the partner it would compare against — makes a
        # pending pick stale, so drop it rather than risk a click resolving against a moved index.
        super().tabRemoved(index)
        self._cancel_compare_pick()

    def _close_tab_at(self, index: int) -> bool:
        """Close the tab at *index*; file tabs prompt on unsaved edits, the device tab is kept."""
        widget = self.widget(index)
        if isinstance(widget, FileTab):
            return self.close_file_tab(widget)
        if widget is not None and not isinstance(widget, HexView):  # compare tab (read-only)
            self.removeTab(index)
            widget.deleteLater()
            return True
        return False

    # --- internals -------------------------------------------------------------

    def _refresh_title(self, tab: FileTab) -> None:
        index = self.indexOf(tab)
        if index >= 0:
            self.setTabText(index, tab.title())
            self.setTabToolTip(index, str(tab.document.path))

    def _on_close_requested(self, index: int) -> None:
        self._close_tab_at(index)

    # --- context menu ----------------------------------------------------------

    def _build_context_menu(self, index: int) -> QMenu:
        menu = QMenu(self)
        open_action = menu.addAction(self.tr("Open File…"))
        open_action.triggered.connect(self.openRequested)
        widget = self.widget(index)
        # "Compare with…" needs a comparable source and at least one other comparable tab to pick.
        if self._is_comparable(widget) and self._comparable_count() >= 2:
            menu.addSeparator()
            compare_action = menu.addAction(self.tr("Compare with…"))
            compare_action.triggered.connect(lambda: self._begin_compare_pick(widget))
        if widget is not None and not isinstance(widget, HexView):  # a closable file/compare tab
            menu.addSeparator()
            close_action = menu.addAction(self.tr("Close"))
            close_action.triggered.connect(lambda: self._close_tab_at(self.indexOf(widget)))
        return menu

    def _show_tab_menu(self, pos) -> None:
        bar = self.tabBar()
        menu = self._build_context_menu(bar.tabAt(pos))
        menu.exec(bar.mapToGlobal(pos))

    # --- compare-with pick -----------------------------------------------------

    def _is_comparable(self, widget: QWidget | None) -> bool:
        """Whether *widget* can take part in a comparison (the device view or a file tab)."""
        return isinstance(widget, (HexView, FileTab))

    def _comparable_count(self) -> int:
        return sum(1 for i in range(self.count()) if self._is_comparable(self.widget(i)))

    def _tab_label(self, widget: QWidget | None) -> str:
        if isinstance(widget, HexView):
            return self.tr("Device memory")
        if isinstance(widget, FileTab):
            return widget.document.path.name
        return ""

    def _begin_compare_pick(self, source: QWidget) -> None:
        self._compare_source = source
        self.tabBar().setCursor(Qt.CursorShape.PointingHandCursor)
        self.logMessage.emit(
            "info", self.tr("Compare: pick a tab to compare “%s” with…") % self._tab_label(source)
        )

    def _cancel_compare_pick(self) -> None:
        if self._compare_source is not None:
            self._compare_source = None
            self.tabBar().unsetCursor()

    def eventFilter(self, watched, event) -> bool:
        # While a compare is armed, the next press on the tab strip resolves the pick.
        if (
            watched is self.tabBar()
            and self._compare_source is not None
            and event.type() == QEvent.Type.MouseButtonPress
        ):
            if event.button() != Qt.MouseButton.LeftButton:
                # A right/middle press cancels the pick but is left to normal handling, so a
                # right-click still opens the context menu — now in a clean, unarmed state.
                self._cancel_compare_pick()
                return super().eventFilter(watched, event)
            index = self.tabBar().tabAt(event.position().toPoint())
            source = self._compare_source
            self._cancel_compare_pick()
            if index >= 0:
                target = self.widget(index)
                if target is not source:
                    self._run_tab_compare(source, target)
            return True  # a left press resolves the pick whether it hit a tab or empty space
        return super().eventFilter(watched, event)

    def _run_tab_compare(self, source: QWidget, target: QWidget) -> None:
        if not self._is_comparable(target):
            self.logMessage.emit("warn", self.tr("That tab cannot be compared."))
            return
        device = next((w for w in (source, target) if isinstance(w, HexView)), None)
        if device is not None:
            # One side is the device: reuse the file tab's own read-and-compare-against-memory path.
            file_tab = target if device is source else source
            if isinstance(file_tab, FileTab):
                file_tab.compare()
            return
        self._compare_file_tabs(source, target)

    def _compare_file_tabs(self, left: FileTab, right: FileTab) -> None:
        # A raw .bin has no address of its own. Place it in the other file's space by prompting for
        # a start address when the other side is addressed; two bare .bins just line up from 0.
        left_bin = left.document.kind is ImageKind.BIN
        right_bin = right.document.kind is ImageKind.BIN
        left_segments = self._operand_segments(left, rebase=left_bin and not right_bin)
        if left_segments is None:
            return  # the base-address prompt was cancelled
        right_segments = self._operand_segments(right, rebase=right_bin and not left_bin)
        if right_segments is None:
            return

        left_name, right_name = left.document.path.name, right.document.path.name
        result = compare_buffers(left_segments, right_segments)
        if not result.segments:
            self.logMessage.emit(
                "warn",
                self.tr("Nothing to compare: %s and %s share no address range.")
                % (left_name, right_name),
            )
            return
        if result.matched:
            summary = self.tr("%s and %s match (%d bytes)") % (
                left_name,
                right_name,
                result.bytes_compared,
            )
        else:
            summary = self.tr("%s and %s differ: %d byte(s) in %d range(s)") % (
                left_name,
                right_name,
                result.bytes_differing,
                result.range_count,
            )
        self.logMessage.emit("info", summary)
        self._open_compare_result(result, left_name, right_name)

    def _operand_segments(self, tab: FileTab, rebase: bool) -> list[tuple[int, bytes]] | None:
        """The file's segments, rebased to a prompted start address when *rebase* is set.

        Returns ``None`` only when *rebase* is set and the user cancels the prompt.
        """
        segments = tab.document.segment_views()
        if not rebase:
            return segments
        base = self._ask_bin_base(tab)
        if base is None:
            return None
        return [(base + addr, data) for addr, data in segments]

    def _ask_bin_base(self, tab: FileTab) -> int | None:
        """Prompt for the start address of a raw .bin, defaulting to the last one used."""
        default = self._settings.last_bin_base_addr if self._settings is not None else _DEFAULT_BASE
        dialog = BaseAddressDialog(
            default, self, self.tr("Compare %s starting at:") % tab.document.path.name
        )
        if dialog.exec() != BaseAddressDialog.DialogCode.Accepted:
            return None
        addr = dialog.base_address()
        if addr is None:
            self.logMessage.emit("error", self.tr("Invalid base address"))
            return None
        if self._settings is not None:
            self._settings.last_bin_base_addr = addr
        return addr
