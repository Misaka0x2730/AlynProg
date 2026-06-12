"""The Memory page's tabbed work area: a pinned device-memory tab plus file and compare tabs.

The first tab always holds the live device hex view and cannot be closed; opening a firmware file
adds a closable :class:`FileTab` beside it, and comparing a file with device memory opens a closable
side-by-side :class:`CompareTab`. The work area keeps file-tab titles in sync with their dirty
marker and routes per-tab log messages up to the window's log pane.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QMenu, QTabBar, QTabWidget, QWidget

from alynprog.core.compare import MemoryCompareResult
from alynprog.core.document import FirmwareDocument
from alynprog.core.session import SessionController
from alynprog.core.settings import Settings
from alynprog.ui.panels.compare_tab import CompareTab
from alynprog.ui.panels.file_tab import FileTab
from alynprog.ui.panels.hexview.view import HexView


class MemoryWorkArea(QTabWidget):
    logMessage = Signal(str, str)  # (level, text)
    openRequested = Signal()  # the user asked to open a file (window owns the file dialog)

    def __init__(
        self,
        device_view: HexView,
        session: SessionController,
        settings: Settings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session = session
        self._settings = settings
        self.setTabsClosable(True)
        self.setMovable(True)
        self.setDocumentMode(True)

        self.addTab(device_view, self.tr("Device memory"))
        self._pin_device_tab()
        self.tabCloseRequested.connect(self._on_close_requested)

        # Right-clicking the tab strip offers Open (and Close on a file tab).
        bar = self.tabBar()
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        bar.customContextMenuRequested.connect(self._show_tab_menu)

    def _pin_device_tab(self) -> None:
        # The device tab is permanent: strip its close button on whichever side the style placed it.
        bar = self.tabBar()
        for side in (QTabBar.ButtonPosition.RightSide, QTabBar.ButtonPosition.LeftSide):
            bar.setTabButton(0, side, None)

    # --- file tabs -------------------------------------------------------------

    def open_document(self, document: FirmwareDocument) -> FileTab:
        tab = FileTab(document, self._session, self._settings, self)
        tab.logMessage.connect(self.logMessage)
        tab.dirtyChanged.connect(lambda _dirty, t=tab: self._refresh_title(t))
        tab.compareReady.connect(self._open_compare_tab)
        index = self.addTab(tab, tab.title())
        self.setTabToolTip(index, str(document.path))
        self.setCurrentIndex(index)
        return tab

    def _open_compare_tab(self, result: MemoryCompareResult, file_label: str) -> CompareTab:
        tab = CompareTab(result, file_label, self._settings, self)
        index = self.addTab(tab, CompareTab.title_for(file_label))
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
            self.removeTab(index)
            tab.deleteLater()
        return True

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
        if widget is not None and not isinstance(widget, HexView):  # a closable file/compare tab
            menu.addSeparator()
            close_action = menu.addAction(self.tr("Close"))
            close_action.triggered.connect(lambda: self._close_tab_at(self.indexOf(widget)))
        return menu

    def _show_tab_menu(self, pos) -> None:
        bar = self.tabBar()
        menu = self._build_context_menu(bar.tabAt(pos))
        menu.exec(bar.mapToGlobal(pos))
