"""A small dialog asking for the start address of a raw image before comparing it with memory.

A ``.bin`` file carries no addresses of its own, so to line it up against device memory the user
must say where it begins. HEX and ELF documents skip this — they already know their addresses.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)


class BaseAddressDialog(QDialog):
    def __init__(self, default_addr: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Base address"))

        self._edit = QLineEdit(f"0x{default_addr:08X}", self)
        self._edit.setPlaceholderText(self.tr("address (0x…)"))

        form = QFormLayout()
        form.addRow(self.tr("Compare the file starting at:"), self._edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def base_address(self) -> int | None:
        """The entered address, or ``None`` if it is blank or not valid hexadecimal."""
        text = self._edit.text().strip()
        if not text:
            return None
        try:
            return int(text, 16)
        except ValueError:
            return None
