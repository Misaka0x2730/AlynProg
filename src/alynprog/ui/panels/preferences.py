"""Preferences dialog: GDB executable path and theme selection."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from alynprog.core.settings import Settings
from alynprog.tools.gdb_locator import find_gdb
from alynprog.ui.theme import ThemeManager, ThemeMode

_THEME_LABELS = [
    (ThemeMode.SYSTEM, "System"),
    (ThemeMode.LIGHT, "Light"),
    (ThemeMode.DARK, "Dark"),
]


class PreferencesDialog(QDialog):
    def __init__(
        self,
        settings: Settings,
        theme: ThemeManager,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Preferences"))
        self._settings = settings
        self._theme = theme

        self._gdb_path = QLineEdit(settings.gdb_path, self)
        browse = QPushButton(self.tr("Browse…"), self)
        browse.clicked.connect(self._browse)
        auto = QPushButton(self.tr("Autodetect"), self)
        auto.clicked.connect(self._autodetect)
        gdb_row = QHBoxLayout()
        gdb_row.addWidget(self._gdb_path, 1)
        gdb_row.addWidget(browse)
        gdb_row.addWidget(auto)
        gdb_wrap = QWidget(self)
        gdb_wrap.setLayout(gdb_row)
        gdb_row.setContentsMargins(0, 0, 0, 0)

        self._theme_box = QComboBox(self)
        for mode, label in _THEME_LABELS:
            self._theme_box.addItem(self.tr(label), mode)
        self._theme_box.setCurrentIndex(
            next((i for i, (m, _) in enumerate(_THEME_LABELS) if m is theme.mode), 0)
        )
        self._theme_box.currentIndexChanged.connect(self._preview_theme)

        form = QFormLayout()
        form.addRow(self.tr("GDB executable:"), gdb_wrap)
        form.addRow(self.tr("Theme:"), self._theme_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr("Select GDB executable"))
        if path:
            self._gdb_path.setText(path)

    def _autodetect(self) -> None:
        info = find_gdb(self._gdb_path.text().strip() or None)
        if info is not None:
            self._gdb_path.setText(info.path)

    def _preview_theme(self) -> None:
        self._theme.apply(self._theme_box.currentData())

    def _accept(self) -> None:
        self._settings.gdb_path = self._gdb_path.text().strip()
        mode = self._theme_box.currentData()
        self._settings.theme_mode = mode.value
        self._theme.apply(mode)
        self.accept()
