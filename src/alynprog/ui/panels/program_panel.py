"""The Erasing & Programming panel: flash an ELF/HEX/BIN, verify, and erase flash."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from alynprog.core.backend import EraseKind, EraseSpec, VerifyMethod
from alynprog.core.sectors import Sector, coalesce_ranges, enumerate_sectors
from alynprog.core.session import SessionController, SessionState
from alynprog.core.settings import Settings

_FILE_FILTER = "Firmware (*.elf *.hex *.ihex *.bin);;All files (*)"


class ProgramPanel(QWidget):
    logMessage = Signal(str, str)

    def __init__(
        self,
        session: SessionController,
        settings: Settings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session = session
        self._settings = settings
        self._build_ui()
        self._wire_session()
        self._update_for_state(session.state)

    def _build_ui(self) -> None:
        self._file_box = QComboBox(self)
        self._file_box.setEditable(True)
        self._file_box.addItems(self._settings.recent_images)
        self._file_box.setCurrentText("")
        self._file_box.editTextChanged.connect(self._on_file_changed)
        browse = QPushButton(self.tr("Browse…"), self)
        browse.clicked.connect(self._browse)
        file_row = QHBoxLayout()
        file_row.addWidget(self._file_box, 1)
        file_row.addWidget(browse)

        self._base_addr = QLineEdit(self)
        self._base_addr.setText(f"0x{self._settings.last_bin_base_addr:08X}")
        self._base_label = QLabel(self.tr("Base address (.bin only):"))

        self._verify = QCheckBox(self.tr("Verify after programming"), self)
        self._verify.setChecked(True)
        self._verify_pref = True  # user's intended verify setting, preserved across backends
        self._verify.toggled.connect(self._on_verify_toggled)
        self._run_after = QCheckBox(self.tr("Run after programming"), self)

        self._program_btn = QPushButton(self.tr("Start programming"), self)
        self._program_btn.clicked.connect(self._on_program)

        self._progress = QProgressBar(self)
        self._progress.setRange(0, 100)

        program_box = QGroupBox(self.tr("Program"), self)
        form = QFormLayout(program_box)
        form.addRow(self.tr("File:"), _wrap(file_row))
        form.addRow(self._base_label, self._base_addr)
        form.addRow("", self._verify)
        form.addRow("", self._run_after)
        form.addRow("", self._program_btn)
        form.addRow(self.tr("Progress:"), self._progress)

        # Erase group: full-chip plus a checkable per-sector list (sectors are computed from the
        # flash blocksize reported by the target's memory map).
        self._mass_erase_btn = QPushButton(self.tr("Full chip erase"), self)
        self._mass_erase_btn.clicked.connect(self._on_mass_erase)

        self._sector_list = QListWidget(self)
        self._sector_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._sector_list.setMaximumHeight(160)
        self._sector_hint = QLabel(self.tr("Attach to a target to list flash sectors."), self)
        self._sector_hint.setWordWrap(True)

        select_all = QPushButton(self.tr("Select all"), self)
        select_all.clicked.connect(lambda: self._set_all_sectors(True))
        clear_all = QPushButton(self.tr("Clear"), self)
        clear_all.clicked.connect(lambda: self._set_all_sectors(False))
        self._erase_sectors_btn = QPushButton(self.tr("Erase selected sectors"), self)
        self._erase_sectors_btn.clicked.connect(self._on_erase_selected_sectors)
        sector_buttons = QHBoxLayout()
        sector_buttons.addWidget(select_all)
        sector_buttons.addWidget(clear_all)
        sector_buttons.addStretch(1)
        sector_buttons.addWidget(self._erase_sectors_btn)

        erase_box = QGroupBox(self.tr("Erase"), self)
        erase_layout = QVBoxLayout(erase_box)
        erase_layout.addWidget(self._mass_erase_btn)
        erase_layout.addWidget(self._sector_hint)
        erase_layout.addWidget(self._sector_list)
        erase_layout.addLayout(sector_buttons)

        self._reset_btn = QPushButton(self.tr("Reset target"), self)
        self._reset_btn.clicked.connect(lambda: self._session.reset(halt=False))

        layout = QVBoxLayout(self)
        layout.addWidget(program_box)
        layout.addWidget(erase_box)
        layout.addWidget(self._reset_btn)
        layout.addStretch(1)
        self._on_file_changed(self._file_box.currentText())

    def _wire_session(self) -> None:
        self._session.state_changed.connect(self._update_for_state)
        self._session.capabilities_changed.connect(self._apply_capabilities)
        self._session.memory_map_changed.connect(self._populate_sectors)
        self._session.progress.connect(self._on_progress)
        self._session.program_done.connect(self._on_program_done)
        self._session.operation_failed.connect(self._on_failed)

    # --- actions ---------------------------------------------------------------

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr("Select firmware"), "", _FILE_FILTER)
        if path:
            self._file_box.setCurrentText(path)

    def _on_file_changed(self, text: str) -> None:
        is_bin = text.lower().endswith(".bin")
        self._base_addr.setEnabled(is_bin)
        self._base_label.setEnabled(is_bin)

    def _on_program(self) -> None:
        path = self._file_box.currentText().strip()
        if not path:
            self.logMessage.emit("warn", self.tr("Select a firmware file first"))
            return
        base = None
        if path.lower().endswith(".bin"):
            base = _parse_hex(self._base_addr.text())
            if base is None:
                self.logMessage.emit("error", self.tr("Invalid base address"))
                return
            self._settings.last_bin_base_addr = base
        self._settings.push_recent_image(path)
        self._progress.setValue(0)
        self.logMessage.emit("info", self.tr("Programming %s…") % Path(path).name)
        self._session.program(
            path, base, verify=self._verify.isChecked(), run_after=self._run_after.isChecked()
        )

    def _on_mass_erase(self) -> None:
        self.logMessage.emit("info", self.tr("Erasing whole chip…"))
        self._session.erase(EraseSpec(kind=EraseKind.MASS))

    def _populate_sectors(self, regions) -> None:
        self._sector_list.clear()
        sectors = enumerate_sectors(list(regions))
        for sector in sectors:
            size_kib = sector.size / 1024
            label = (
                f"{sector.region_name} #{sector.index}  "
                f"0x{sector.addr:08X}-0x{sector.end - 1:08X}  ({size_kib:g} KiB)"
            )
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, sector)
            self._sector_list.addItem(item)
        has_sectors = bool(sectors)
        self._sector_hint.setVisible(not has_sectors)
        self._sector_list.setVisible(has_sectors)

    def _set_all_sectors(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self._sector_list.count()):
            self._sector_list.item(row).setCheckState(state)

    def _checked_sectors(self) -> list[Sector]:
        sectors: list[Sector] = []
        for row in range(self._sector_list.count()):
            item = self._sector_list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                sectors.append(item.data(Qt.ItemDataRole.UserRole))
        return sectors

    def _on_erase_selected_sectors(self) -> None:
        sectors = self._checked_sectors()
        if not sectors:
            self.logMessage.emit("warn", self.tr("Select at least one sector to erase"))
            return
        ranges = coalesce_ranges(sectors)
        total = sum(length for _, length in ranges)
        self.logMessage.emit(
            "info",
            self.tr("Erasing %d sector(s) in %d range(s) (%d bytes)…")
            % (len(sectors), len(ranges), total),
        )
        for addr, length in ranges:
            self._session.erase(EraseSpec(kind=EraseKind.RANGE, addr=addr, length=length))

    # --- session reactions -----------------------------------------------------

    def _on_progress(self, op: str, done: int, total: int) -> None:
        if op == "program" and total:
            self._progress.setValue(int(done * 100 / total))

    def _on_program_done(self, result) -> None:
        if result.verified is True:
            self.logMessage.emit(
                "info", self.tr("Programming verified OK (%d bytes)") % result.bytes_written
            )
        elif result.verified is False:
            mism = ", ".join(s.name for s in result.sections if not s.matched)
            self.logMessage.emit("error", self.tr("Verification FAILED: %s") % (mism or "mismatch"))
        else:
            self.logMessage.emit(
                "info", self.tr("Programming complete (%d bytes)") % result.bytes_written
            )
        self._progress.setValue(100)

    def _on_failed(self, op: str, message: str) -> None:
        if op in ("program", "erase", "reset", "save"):
            self.logMessage.emit("error", self.tr("%s failed: %s") % (op, message))

    def _apply_capabilities(self, caps) -> None:
        attached = self._session.state == SessionState.ATTACHED
        # Some backends (pyOCD) do not verify after programming; don't offer a checkbox that lies.
        # Restore the user's preference when verify is available again (blockSignals so the
        # programmatic setChecked doesn't overwrite that preference).
        verifies = caps.verify_method is not VerifyMethod.NONE
        self._verify.blockSignals(True)
        self._verify.setEnabled(verifies)
        self._verify.setChecked(verifies and self._verify_pref)
        self._verify.blockSignals(False)
        self._verify.setToolTip(
            "" if verifies else self.tr("This backend does not verify after programming")
        )
        self._mass_erase_btn.setEnabled(attached and caps.supports_mass_erase)
        self._mass_erase_btn.setToolTip(
            "" if caps.supports_mass_erase else self.tr("Not supported by this probe/target")
        )
        sector = caps.supports_sector_erase
        self._erase_sectors_btn.setEnabled(attached and sector)
        self._sector_list.setEnabled(sector)
        self._erase_sectors_btn.setToolTip(
            "" if sector else self.tr("Sector erase not supported here")
        )

    def _on_verify_toggled(self, checked: bool) -> None:
        # Only user toggles reach here (programmatic changes block signals), so this tracks intent.
        self._verify_pref = checked

    def _update_for_state(self, state: SessionState) -> None:
        if state == SessionState.DISCONNECTED:
            # No capabilities while disconnected: restore the verify checkbox to the user's choice.
            self._verify.blockSignals(True)
            self._verify.setEnabled(True)
            self._verify.setChecked(self._verify_pref)
            self._verify.blockSignals(False)
            self._verify.setToolTip("")
        attached = state == SessionState.ATTACHED
        self._program_btn.setEnabled(attached)
        self._reset_btn.setEnabled(state in (SessionState.CONNECTED, SessionState.ATTACHED))
        caps = self._session.capabilities
        mass = bool(caps and caps.supports_mass_erase)
        sector = bool(caps and caps.supports_sector_erase)
        self._mass_erase_btn.setEnabled(attached and mass)
        self._erase_sectors_btn.setEnabled(attached and sector)


def _parse_hex(text: str) -> int | None:
    text = text.strip()
    if not text:
        return None
    try:
        return int(text, 16)
    except ValueError:
        return None


def _wrap(layout) -> QWidget:
    w = QWidget()
    layout.setContentsMargins(0, 0, 0, 0)
    w.setLayout(layout)
    return w
