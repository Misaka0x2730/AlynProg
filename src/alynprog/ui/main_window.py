"""The main window: STM32CubeProgrammer-style layout assembled from the panels.

Layout: a thin **left** vertical tab strip selects the central page (Memory / Programming); the
**right** dock hosts the target-configuration (connect) panel; the **bottom** dock hosts the log.
The window owns the :class:`SessionController` and routes its log and error signals to the log pane.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDockWidget,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

from alynprog import APP_NAME
from alynprog.core.session import SessionController, SessionState
from alynprog.core.settings import Settings
from alynprog.ui.panels.connect_panel import ConnectPanel
from alynprog.ui.panels.hexview import HexView
from alynprog.ui.panels.log_panel import LogPanel
from alynprog.ui.panels.preferences import PreferencesDialog
from alynprog.ui.panels.program_panel import ProgramPanel
from alynprog.ui.theme import ThemeManager, ThemeMode


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
        self._pages.addWidget(self._hexview)
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
        file_menu = self.menuBar().addMenu(self.tr("&File"))
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
        self._program.logMessage.connect(self._log.message)

    # --- actions ---------------------------------------------------------------

    def _open_preferences(self) -> None:
        dialog = PreferencesDialog(self._settings, self._theme, self)
        dialog.exec()

    def _set_theme(self, mode: ThemeMode) -> None:
        self._theme.apply(mode)
        self._settings.theme_mode = mode.value

    def _about(self) -> None:
        from PySide6.QtWidgets import QMessageBox

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
        self._settings.window_geometry = self.saveGeometry()
        self._settings.window_state = self.saveState()
        self._settings.sync()
        self._session.shutdown()
        super().closeEvent(event)
