"""The always-visible bottom log pane.

Shows app-level messages (info/warn/error) always, and raw GDB/MI + monitor traffic when the
"Show raw protocol" toggle is on. Each line is timestamped. The log is the user's window into what
the probe is actually doing.
"""

from __future__ import annotations

import time

from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# Transport stream names that are considered "raw protocol" and hidden unless the toggle is on.
_RAW_STREAMS = {"command", "console", "log", "target", "output", "stderr"}

_LEVEL_COLORS = {
    "error": "#e06c75",
    "warn": "#e5c07b",
    "info": "#56b6c2",
    "command": "#98c379",
    "stderr": "#e06c75",
}


class LogPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._show_raw = False

        self._view = QPlainTextEdit(self)
        self._view.setReadOnly(True)
        self._view.setMaximumBlockCount(10000)
        font = self._view.font()
        font.setStyleHint(font.StyleHint.Monospace)
        font.setFamily("monospace")
        self._view.setFont(font)

        self._raw_toggle = QCheckBox(self.tr("Show raw protocol"), self)
        self._raw_toggle.toggled.connect(self._on_toggle_raw)
        clear_btn = QPushButton(self.tr("Clear"), self)
        clear_btn.clicked.connect(self._view.clear)
        save_btn = QPushButton(self.tr("Save…"), self)
        save_btn.clicked.connect(self._save)

        controls = QHBoxLayout()
        controls.addWidget(self._raw_toggle)
        controls.addStretch(1)
        controls.addWidget(clear_btn)
        controls.addWidget(save_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(controls)
        layout.addWidget(self._view)

    # --- public API ------------------------------------------------------------

    def message(self, level: str, text: str) -> None:
        """Log an app-level message (always shown)."""
        self._append(level, text)

    def log_stream(self, stream: str, text: str) -> None:
        """Log a transport stream line (shown only when raw protocol is enabled)."""
        if stream in _RAW_STREAMS and not self._show_raw:
            return
        self._append(stream, text)

    # --- internals -------------------------------------------------------------

    def _on_toggle_raw(self, checked: bool) -> None:
        self._show_raw = checked

    def _append(self, level: str, text: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        cursor = self._view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        color = _LEVEL_COLORS.get(level)
        if color:
            fmt.setForeground(QColor(color))
        cursor.insertText(f"{timestamp} [{level}] {text}\n", fmt)
        self._view.setTextCursor(cursor)
        self._view.ensureCursorVisible()

    def _save(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Save log"), "alynprog.log", self.tr("Log files (*.log *.txt)")
        )
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self._view.toPlainText())
