"""The main window: the panel layout, menus and session wiring.

Layout: a thin **left** vertical tab strip selects the central page (Memory / Programming); the
**right** dock hosts the target-configuration (connect) panel; the **bottom** dock hosts the log.
The Memory page is a tabbed work area (the live device hex view plus any opened firmware files).
The window owns the :class:`SessionController` and routes its log and error signals to the log pane.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
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
from alynprog.ui.panels.hexview import HexView
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

        self._log = LogPanel(self)
        self._hexview = HexView(self._session, self._settings, self)
        self._workarea = MemoryWorkArea(self._hexview, self._session, self._settings, self)
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

        self._tabstrip = QListWidget(self)
        self._tabstrip.setFixedWidth(120)
        self._tabstrip.setIconSize(QSize(24, 24))
        for label in (self.tr("Memory"), self.tr("Programming")):
            QListWidgetItem(label, self._tabstrip)
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
        view_menu.addAction(self._config_dock.toggleViewAction())
        view_menu.addAction(self._log_dock.toggleViewAction())

        help_menu = self.menuBar().addMenu(self.tr("&Help"))
        about = help_menu.addAction(self.tr("About AlynProg"))
        about.setMenuRole(about.MenuRole.AboutRole)
        about.triggered.connect(self._about)

    def _build_statusbar(self) -> None:
        self.statusBar().showMessage(self.tr("Disconnected"))

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

        QMessageBox.about(
            self,
            self.tr("About AlynProg"),
            self.tr(
                "AlynProg %s\n\nCross-platform microcontroller flashing utility.\n"
                "Backend: Black Magic Probe via GDB/MI."
            )
            % __version__,
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
