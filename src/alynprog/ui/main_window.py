"""The main window: the panel layout, menus and session wiring.

Layout: a thin **left** vertical tab strip selects the central page (Memory / Programming); the
**right** dock hosts the target-configuration (connect) panel; the **bottom** dock hosts the log.
The Memory page is a tabbed work area (the live device hex view plus any opened firmware files).
The window owns the :class:`SessionController` and routes its log and error signals to the log pane.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QWidget,
)

from alynprog import APP_NAME
from alynprog.core.document import FirmwareDocument
from alynprog.core.errors import ImageError
from alynprog.core.session import SessionController, SessionState
from alynprog.core.settings import Settings
from alynprog.ui.panels.connect_panel import ConnectPanel
from alynprog.ui.panels.hexview import HexFontController, HexView
from alynprog.ui.panels.log_panel import LogPanel
from alynprog.ui.panels.preferences import PreferencesDialog
from alynprog.ui.panels.program_panel import ProgramPanel
from alynprog.ui.panels.workarea import MemoryWorkArea
from alynprog.ui.theme import ThemeManager, ThemeMode

_OPEN_FILTER = "Firmware (*.bin *.hex *.ihex *.ihx *.elf);;All files (*)"


class MainWindow(QMainWindow):
    def __init__(
        self,
        theme: ThemeManager,
        settings: Settings,
        *,
        use_fake: bool = False,
    ) -> None:
        super().__init__()
        self._theme = theme
        self._settings = settings
        tagline = self.tr("Simple Microcontroller Flash Tool")
        self.setWindowTitle(f"{APP_NAME} — {tagline}")
        self.resize(1100, 720)

        self._session = SessionController(self)

        # One shared font size for every hex pane (device view, file tabs, compare halves), driven
        # by the status-bar slider and the View-menu zoom actions and persisted across runs.
        self._hex_font = HexFontController(self._settings, self)

        self._log = LogPanel(self)
        self._hexview = HexView(self._session, self._settings, self._hex_font, self)
        self._workarea = MemoryWorkArea(
            self._hexview, self._session, self._settings, self._hex_font, self
        )
        self._program = ProgramPanel(self._session, self._settings, self)
        self._connect = ConnectPanel(self._session, self._settings, use_fake=use_fake, parent=self)

        self._build_central()
        self._build_docks()
        self._build_menus()
        self._build_statusbar()
        self._wire_signals()

        self._restore_window_state()

    # --- construction ----------------------------------------------------------

    def _build_central(self) -> None:
        self._pages = QStackedWidget(self)
        self._pages.addWidget(self._workarea)
        self._pages.addWidget(self._program)

        # A slim icon rail: each page is a centred, theme-coloured glyph (label kept as a tooltip).
        self._tabstrip = QListWidget(self)
        self._tabstrip.setViewMode(QListView.ViewMode.IconMode)
        self._tabstrip.setMovement(QListView.Movement.Static)
        self._tabstrip.setFlow(QListView.Flow.TopToBottom)
        self._tabstrip.setWrapping(False)
        self._tabstrip.setDragDropMode(QListWidget.DragDropMode.NoDragDrop)
        self._tabstrip.setUniformItemSizes(True)
        self._tabstrip.setSpacing(2)
        self._tabstrip.setIconSize(QSize(26, 26))
        self._tabstrip.setFixedWidth(64)
        # The rail is exactly item-width; suppress the stray horizontal scrollbar IconMode would
        # otherwise show when the item plus frame margins nudge past the viewport.
        self._tabstrip.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._nav_items: list[QListWidgetItem] = []
        for tooltip in (self.tr("Memory"), self.tr("Programming")):
            item = QListWidgetItem(self._tabstrip)
            item.setToolTip(tooltip)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, tooltip)
            # A full-strip-width hint lets IconMode centre the glyph horizontally on the rail.
            item.setSizeHint(QSize(60, 46))
            self._nav_items.append(item)
        self._refresh_nav_icons()

        self._tabstrip.setCurrentRow(0)
        self._tabstrip.currentRowChanged.connect(self._pages.setCurrentIndex)
        self._tabstrip.currentRowChanged.connect(self._on_tab_changed)

        central = QWidget(self)
        from PySide6.QtWidgets import QHBoxLayout

        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tabstrip)
        layout.addWidget(self._pages, 1)
        self.setCentralWidget(central)

    def _refresh_nav_icons(self) -> None:
        """(Re)build the nav-rail icons in the current palette's text/highlight colours."""
        from PySide6.QtGui import QPalette

        from alynprog.ui.icons import MEMORY_SVG, PROGRAMMING_SVG, nav_icon

        palette = self._tabstrip.palette()
        normal = palette.color(QPalette.ColorRole.Text)
        selected = palette.color(QPalette.ColorRole.HighlightedText)
        for item, svg in zip(self._nav_items, (MEMORY_SVG, PROGRAMMING_SVG), strict=True):
            item.setIcon(nav_icon(svg, normal, selected))

    def changeEvent(self, event) -> None:
        # The nav-rail glyphs are baked pixmaps, so re-tint them whenever the palette flips
        # (theme menu, Preferences, or the OS switching light/dark under "System").
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and getattr(self, "_nav_items", None):
            self._refresh_nav_icons()

    def _build_docks(self) -> None:
        self._config_dock = QDockWidget(self.tr("Target configuration"), self)
        self._config_dock.setObjectName("configDock")
        self._config_dock.setWidget(self._connect)
        self._config_dock.setAllowedAreas(
            Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._config_dock)

        self._log_dock = QDockWidget(self.tr("Log"), self)
        self._log_dock.setObjectName("logDock")
        self._log_dock.setWidget(self._log)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._log_dock)

    def _build_menus(self) -> None:
        from PySide6.QtGui import QKeySequence

        file_menu = self.menuBar().addMenu(self.tr("&File"))
        open_action = file_menu.addAction(self.tr("Open…"))
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._open_file)

        self._recent_menu = file_menu.addMenu(self.tr("Open Recent"))
        self._recent_menu.aboutToShow.connect(self._rebuild_recent_menu)

        self._save_action = file_menu.addAction(self.tr("Save"))
        self._save_action.setShortcut(QKeySequence.StandardKey.Save)
        self._save_action.triggered.connect(self._save_current)
        self._save_as_action = file_menu.addAction(self.tr("Save As…"))
        self._save_as_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        self._save_as_action.triggered.connect(self._save_current_as)
        self._close_tab_action = file_menu.addAction(self.tr("Close Tab"))
        self._close_tab_action.setShortcut(QKeySequence.StandardKey.Close)
        self._close_tab_action.triggered.connect(self._close_current_tab)

        file_menu.addSeparator()
        prefs = file_menu.addAction(self.tr("Preferences…"))
        prefs.setMenuRole(prefs.MenuRole.PreferencesRole)
        prefs.triggered.connect(self._open_preferences)
        file_menu.addSeparator()
        quit_action = file_menu.addAction(self.tr("Quit"))
        quit_action.setMenuRole(quit_action.MenuRole.QuitRole)
        quit_action.triggered.connect(self.close)

        view_menu = self.menuBar().addMenu(self.tr("&View"))
        theme_menu = view_menu.addMenu(self.tr("Theme"))
        from PySide6.QtGui import QActionGroup

        group = QActionGroup(self)
        group.setExclusive(True)
        for mode, label in (
            (ThemeMode.SYSTEM, self.tr("System")),
            (ThemeMode.LIGHT, self.tr("Light")),
            (ThemeMode.DARK, self.tr("Dark")),
        ):
            action = theme_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(mode is self._theme.mode)
            action.triggered.connect(lambda _checked, m=mode: self._set_theme(m))
            group.addAction(action)

        view_menu.addSeparator()
        zoom_in = view_menu.addAction(self.tr("Increase Font Size"))
        zoom_in.setShortcut(QKeySequence.StandardKey.ZoomIn)
        zoom_in.triggered.connect(lambda: self._hex_font.step(1))
        zoom_out = view_menu.addAction(self.tr("Decrease Font Size"))
        zoom_out.setShortcut(QKeySequence.StandardKey.ZoomOut)
        zoom_out.triggered.connect(lambda: self._hex_font.step(-1))
        zoom_reset = view_menu.addAction(self.tr("Reset Font Size"))
        zoom_reset.setShortcut("Ctrl+0")
        zoom_reset.triggered.connect(self._hex_font.reset)

        view_menu.addSeparator()
        view_menu.addAction(self._config_dock.toggleViewAction())
        view_menu.addAction(self._log_dock.toggleViewAction())

        help_menu = self.menuBar().addMenu(self.tr("&Help"))
        about = help_menu.addAction(self.tr("About AlynProg"))
        about.setMenuRole(about.MenuRole.AboutRole)
        about.triggered.connect(self._about)

    def _build_statusbar(self) -> None:
        self.statusBar().showMessage(self.tr("Disconnected"))
        self._build_zoom_control()

    def _build_zoom_control(self) -> None:
        """A small font-size slider, pinned to the right of the status bar, for the hex views.

        The caption, slider and readout are added as three separate permanent widgets so the status
        bar sizes each to its own width and keeps them snug; wrapping them in a stretched container
        would let the labels expand and drift the caption away from the slider.
        """
        from PySide6.QtWidgets import QLabel, QSlider

        from alynprog.ui.panels.hexview.font import MAX_POINT_SIZE, MIN_POINT_SIZE

        caption = QLabel(self.tr("Font:"), self)
        self._zoom_slider = QSlider(Qt.Orientation.Horizontal, self)
        self._zoom_slider.setRange(MIN_POINT_SIZE, MAX_POINT_SIZE)
        self._zoom_slider.setValue(self._hex_font.point_size)
        self._zoom_slider.setFixedWidth(120)
        self._zoom_slider.setToolTip(self.tr("Memory / file view font size"))
        self._zoom_value = QLabel(self)
        self._zoom_value.setMinimumWidth(self._zoom_value.fontMetrics().horizontalAdvance("00 pt"))

        self.statusBar().addPermanentWidget(caption)
        self.statusBar().addPermanentWidget(self._zoom_slider)
        self.statusBar().addPermanentWidget(self._zoom_value)

        self._zoom_slider.valueChanged.connect(self._hex_font.set_point_size)
        self._hex_font.sizeChanged.connect(self._on_hex_font_size_changed)
        self._on_hex_font_size_changed(self._hex_font.point_size)

    def _on_hex_font_size_changed(self, point_size: int) -> None:
        # Reflect the size back onto the slider (it may have changed via a zoom action or
        # Ctrl+wheel) without bouncing the signal back into the controller.
        if self._zoom_slider.value() != point_size:
            self._zoom_slider.blockSignals(True)
            self._zoom_slider.setValue(point_size)
            self._zoom_slider.blockSignals(False)
        self._zoom_value.setText(self.tr("%d pt") % point_size)

    def _wire_signals(self) -> None:
        self._session.log_line.connect(self._log.log_stream)
        self._session.operation_failed.connect(
            lambda op, msg: self._log.message("error", f"{op}: {msg}")
        )
        self._session.operation_succeeded.connect(
            lambda op: self._log.message("info", self.tr("%s done") % op)
        )
        self._session.state_changed.connect(self._on_state)
        self._hexview.logMessage.connect(self._log.message)
        self._workarea.logMessage.connect(self._log.message)
        self._workarea.openRequested.connect(self._open_file)
        self._workarea.currentChanged.connect(self._update_file_actions)
        self._program.logMessage.connect(self._log.message)
        self._update_file_actions()

    # --- firmware-file tabs -----------------------------------------------------

    def _open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr("Open firmware file"), "", _OPEN_FILTER)
        if path:
            self._open_path(path)

    def _open_path(self, path: str) -> None:
        try:
            document = FirmwareDocument.open(path)
        except (OSError, ImageError) as exc:
            self._log.message("error", self.tr("Cannot open %s: %s") % (Path(path).name, exc))
            return
        self._workarea.open_document(document)
        self._settings.push_recent_file(str(document.path))
        self._log.message("info", self.tr("Opened %s") % document.path.name)
        self._tabstrip.setCurrentRow(0)  # bring the Memory page forward
        self._update_file_actions()

    def _rebuild_recent_menu(self) -> None:
        self._recent_menu.clear()
        recents = [p for p in self._settings.recent_files if Path(p).is_file()]
        if not recents:
            empty = self._recent_menu.addAction(self.tr("(no recent files)"))
            empty.setEnabled(False)
            return
        for path in recents:
            action = self._recent_menu.addAction(path)
            action.triggered.connect(lambda _checked, p=path: self._open_path(p))

    def _save_current(self) -> None:
        tab = self._workarea.current_file_tab()
        if tab is not None:
            tab.save()

    def _save_current_as(self) -> None:
        tab = self._workarea.current_file_tab()
        if tab is not None:
            tab.save_as()

    def _close_current_tab(self) -> None:
        tab = self._workarea.current_file_tab()
        if tab is not None:
            self._workarea.close_file_tab(tab)

    def _update_file_actions(self, _index: int = 0) -> None:
        has_file_tab = self._workarea.current_file_tab() is not None
        self._save_action.setEnabled(has_file_tab)
        self._save_as_action.setEnabled(has_file_tab)
        self._close_tab_action.setEnabled(has_file_tab)

    # --- actions ---------------------------------------------------------------

    def _open_preferences(self) -> None:
        dialog = PreferencesDialog(self._settings, self._theme, self)
        dialog.exec()

    def _set_theme(self, mode: ThemeMode) -> None:
        self._theme.apply(mode)
        self._settings.theme_mode = mode.value

    def _about(self) -> None:
        from alynprog import __version__

        repo_url = "https://github.com/Misaka0x2730/AlynProg"
        description = self.tr("Universal MCU programmer with pyOCD and Black Magic Probe support.")
        # Rich text so the repository link renders as a clickable, browser-opening hyperlink.
        QMessageBox.about(
            self,
            self.tr("About AlynProg"),
            f"<p><b>AlynProg</b> {__version__}</p>"
            f"<p>{description}</p>"
            f'<p><a href="{repo_url}">{repo_url}</a></p>',
        )

    def _on_state(self, state: SessionState) -> None:
        self.statusBar().showMessage(self.tr("State: %s") % state.value)

    def _on_tab_changed(self, _row: int) -> None:
        # Re-read device memory when switching tabs while attached, so the hex view reflects the
        # device (e.g. after programming on the Programming tab).
        if self._session.state is SessionState.ATTACHED:
            self._hexview.refresh()

    # --- window state ----------------------------------------------------------

    def _restore_window_state(self) -> None:
        geometry = self._settings.window_geometry
        if not geometry.isEmpty():
            self.restoreGeometry(geometry)
        state = self._settings.window_state
        if not state.isEmpty():
            self.restoreState(state)

    def closeEvent(self, event) -> None:
        for tab in self._workarea.file_tabs():
            if not tab.maybe_save():
                event.ignore()
                return
        self._settings.window_geometry = self.saveGeometry()
        self._settings.window_state = self.saveState()
        self._settings.sync()
        self._session.shutdown()
        super().closeEvent(event)
