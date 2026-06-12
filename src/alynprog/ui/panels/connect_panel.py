"""The right-hand "Target configuration" panel: probe selection, GDB path, connect/scan/attach.

This panel drives the :class:`SessionController` directly and reflects session state by enabling or
disabling its controls. Capability flags reported after connect gate the target-power and
connect-under-reset options.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from alynprog.core.backend import ConnectOptions, ProbeInfo
from alynprog.core.registry import discover_all
from alynprog.core.session import SessionController, SessionState
from alynprog.core.settings import Settings
from alynprog.tools.gdb_locator import find_gdb


class ConnectPanel(QWidget):
    def __init__(
        self,
        session: SessionController,
        settings: Settings,
        *,
        use_fake: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._session = session
        self._settings = settings
        self._use_fake = use_fake
        self._prev_state = session.state
        self._chosen_target = ""  # pyOCD target name chosen via the picker dialog
        self._pending_autoattach = False  # auto-attach the single pyOCD target after a connect

        # Size to content height (don't grab extra vertical space) so the dock fits the panel and
        # the stacked Log dock takes the rest — notably when the Targets box is hidden for pyOCD.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        self._build_ui()
        self._wire_session()
        self.refresh_probes()
        self._update_for_state(session.state)

    # --- UI construction -------------------------------------------------------

    def _build_ui(self) -> None:
        self._probe_box = QComboBox(self)
        refresh_btn = QPushButton(self.tr("Refresh"), self)
        refresh_btn.clicked.connect(self.refresh_probes)
        probe_row = QHBoxLayout()
        probe_row.addWidget(self._probe_box, 1)
        probe_row.addWidget(refresh_btn)

        self._port_override = QLineEdit(self)
        self._port_override.setPlaceholderText(self.tr("auto (override device path if needed)"))
        self._port_override.setText(self._settings.port_override)

        self._gdb_path = QLineEdit(self)
        self._gdb_path.setText(self._settings.gdb_path)
        self._gdb_path.setPlaceholderText(self.tr("path to arm-none-eabi-gdb"))
        browse_btn = QPushButton(self.tr("Browse…"), self)
        browse_btn.clicked.connect(self._browse_gdb)
        auto_btn = QPushButton(self.tr("Autodetect"), self)
        auto_btn.clicked.connect(self._autodetect_gdb)
        gdb_row = QHBoxLayout()
        gdb_row.addWidget(self._gdb_path, 1)
        gdb_row.addWidget(browse_btn)
        gdb_row.addWidget(auto_btn)

        self._transport = QComboBox(self)
        self._transport.addItems(["swd", "jtag"])
        # Keep the combo at its natural width instead of stretching across the form.
        transport_row = QHBoxLayout()
        transport_row.addWidget(self._transport)
        transport_row.addStretch(1)

        # SWD/JTAG interface clock, entered in kHz (10 kHz … 1000 MHz), 100 kHz steps.
        self._speed = QSpinBox(self)
        self._speed.setSuffix(self.tr(" kHz"))
        self._speed.setRange(10, 1_000_000)
        self._speed.setSingleStep(100)
        self._speed.setValue(self._settings.interface_speed_hz // 1000)
        # Keep the spinbox at its natural width instead of stretching across the form.
        speed_row = QHBoxLayout()
        speed_row.addWidget(self._speed)
        speed_row.addStretch(1)

        # Transport combo and speed spinbox sit one under the other — give them the same width.
        for widget in (self._transport, self._speed):
            widget.ensurePolished()
        field_width = max(self._transport.sizeHint().width(), self._speed.sizeHint().width())
        self._transport.setFixedWidth(field_width)
        self._speed.setFixedWidth(field_width)

        # Target power + a settle delay (ms) shown only while tpwr is enabled.
        self._tpwr = _checkbox(self.tr("Provide target power (tpwr)"), self._settings.target_power)
        self._tpwr.toggled.connect(self._on_tpwr_toggled)
        self._tpwr_delay_label = QLabel(self.tr("settle:"), self)
        self._tpwr_delay = QSpinBox(self)
        self._tpwr_delay.setSuffix(self.tr(" ms"))
        self._tpwr_delay.setRange(0, 60_000)
        self._tpwr_delay.setSingleStep(50)
        self._tpwr_delay.setValue(self._settings.tpwr_delay_ms)
        tpwr_row = QHBoxLayout()
        tpwr_row.addWidget(self._tpwr)
        tpwr_row.addWidget(self._tpwr_delay_label)
        tpwr_row.addWidget(self._tpwr_delay)
        tpwr_row.addStretch(1)

        self._cur = _checkbox(self.tr("Connect under reset"), self._settings.connect_under_reset)

        # Wrapped rows whose visibility depends on the selected backend (see _on_probe_changed).
        self._gdb_wrap = _wrap(gdb_row)
        self._tpwr_wrap = _wrap(tpwr_row)

        # pyOCD target picker row (shown only for pyOCD probes, which can't auto-detect the target).
        self._target_field = QLineEdit(self)
        self._target_field.setReadOnly(True)
        self._target_field.setPlaceholderText(self.tr("No target selected"))
        self._choose_target_btn = QPushButton(self.tr("Choose Target…"), self)
        self._choose_target_btn.clicked.connect(self._on_choose_target)
        target_row = QHBoxLayout()
        target_row.addWidget(self._target_field, 1)
        target_row.addWidget(self._choose_target_btn)
        self._target_wrap = _wrap(target_row)

        self._form = QFormLayout()
        form = self._form
        form.addRow(self.tr("Probe:"), _wrap(probe_row))
        form.addRow(self.tr("Target:"), self._target_wrap)
        form.addRow(self.tr("Port override:"), self._port_override)
        form.addRow(self.tr("GDB:"), self._gdb_wrap)
        form.addRow(self.tr("Transport:"), _wrap(transport_row))
        form.addRow(self.tr("Interface speed:"), _wrap(speed_row))
        form.addRow("", self._tpwr_wrap)
        form.addRow("", self._cur)
        self._on_tpwr_toggled(self._tpwr.isChecked())
        self._probe_box.currentIndexChanged.connect(self._on_probe_changed)

        self._connect_btn = QPushButton(self.tr("Connect"), self)
        self._connect_btn.clicked.connect(self._on_connect)
        self._disconnect_btn = QPushButton(self.tr("Disconnect"), self)
        self._disconnect_btn.clicked.connect(self._session.disconnect_probe)
        conn_row = QHBoxLayout()
        conn_row.addWidget(self._connect_btn)
        conn_row.addWidget(self._disconnect_btn)

        self._scan_btn = QPushButton(self.tr("Scan targets"), self)
        self._scan_btn.clicked.connect(self._session.scan)
        self._attach_btn = QPushButton(self.tr("Attach"), self)
        self._attach_btn.clicked.connect(self._on_attach)
        scan_row = QHBoxLayout()
        scan_row.addWidget(self._scan_btn)
        scan_row.addWidget(self._attach_btn)

        self._targets = QListWidget(self)
        self._targets.itemDoubleClicked.connect(self._on_target_double_clicked)
        self._targets_box = QGroupBox(self.tr("Targets"), self)
        targets_layout = QVBoxLayout(self._targets_box)
        targets_layout.addLayout(scan_row)
        targets_layout.addWidget(self._targets)

        self._status = QLabel(self.tr("Disconnected"), self)
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(conn_row)
        layout.addWidget(self._targets_box)
        layout.addWidget(self._status)
        layout.addStretch(1)

    def _wire_session(self) -> None:
        self._session.state_changed.connect(self._update_for_state)
        self._session.targets_found.connect(self._populate_targets)
        self._session.capabilities_changed.connect(self._apply_capabilities)
        self._session.operation_failed.connect(self._on_operation_failed)

    # --- actions ---------------------------------------------------------------

    def refresh_probes(self) -> None:
        # Repopulate without firing currentIndexChanged per item; settle once at the end.
        self._probe_box.blockSignals(True)
        self._probe_box.clear()
        probes = discover_all(include_fake=self._use_fake)
        for probe in probes:
            label = probe.display_name
            if probe.port_ambiguous:
                label += self.tr(" — port ambiguous")
            self._probe_box.addItem(label, probe)
        if not probes:
            self._probe_box.addItem(self.tr("No probes found"), None)
        self._probe_box.blockSignals(False)
        self._on_probe_changed()

    def _selected_probe(self) -> ProbeInfo | None:
        probe = self._probe_box.currentData()
        if probe is None:
            return None
        override = self._port_override.text().strip()
        # The port override is a BMP-style device-path tweak; it is meaningless (and harmful) for a
        # pyOCD probe whose identifier is a USB unique id.
        if override and probe.backend != "pyocd":
            probe = ProbeInfo(
                backend=probe.backend,
                identifier=override,
                display_name=probe.display_name,
                serial=probe.serial,
            )
        return probe

    def _on_connect(self) -> None:
        probe = self._selected_probe()
        if probe is None:
            self._status.setText(self.tr("Select a probe first"))
            return
        # pyOCD's chosen target is unique, so attach to it automatically once the post-connect
        # scan reports it (see _populate_targets).
        self._pending_autoattach = probe.backend == "pyocd"
        options = ConnectOptions(
            transport=self._transport.currentText(),
            target_power=self._tpwr.isChecked(),
            tpwr_delay_ms=self._tpwr_delay.value(),
            connect_under_reset=self._cur.isChecked(),
            interface_speed_hz=self._speed_hz(),
            target_name=self._chosen_target,
        )
        self._persist()
        self._session.connect_probe(probe.backend, probe, options, self._gdb_path.text().strip())

    def _speed_hz(self) -> int:
        return self._speed.value() * 1000

    def _on_tpwr_toggled(self, checked: bool) -> None:
        self._tpwr_delay_label.setVisible(checked)
        self._tpwr_delay.setVisible(checked)

    # --- pyOCD target selection ------------------------------------------------

    def _on_probe_changed(self, *_args) -> None:
        probe = self._probe_box.currentData()
        is_pyocd = probe is not None and probe.backend == "pyocd"
        # pyOCD has no GDB/port/tpwr; it does need a manually chosen target instead.
        self._form.setRowVisible(self._target_wrap, is_pyocd)
        self._form.setRowVisible(self._port_override, not is_pyocd)
        self._form.setRowVisible(self._gdb_wrap, not is_pyocd)
        self._form.setRowVisible(self._tpwr_wrap, not is_pyocd)
        # pyOCD has a single, manually chosen target attached automatically on connect, so the
        # scan/attach target list is pointless for it.
        self._targets_box.setVisible(not is_pyocd)
        # The panel's status line only echoes session state (already in the window's status bar) and
        # GDB-path feedback (N/A to pyOCD), so it is just clutter for pyOCD probes.
        self._status.setVisible(not is_pyocd)
        self._chosen_target = self._settings.pyocd_last_target(probe.serial) if is_pyocd else ""
        self._target_field.setText(self._chosen_target)
        self._update_connect_enabled()

    def _on_choose_target(self) -> None:
        probe = self._probe_box.currentData()
        if probe is None or probe.backend != "pyocd":
            return
        from alynprog.backends.pyocd.targets import TargetCatalog, default_cache_path
        from alynprog.ui.panels.target_dialog import TargetSelectDialog

        dialog = TargetSelectDialog(
            self,
            settings=self._settings,
            probe_serial=probe.serial,
            catalog=TargetCatalog(default_cache_path()),
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            record = dialog.selected_target()
            if record is not None:
                self._chosen_target = record.name
                self._target_field.setText(record.name)
                self._update_connect_enabled()

    def _update_connect_enabled(self) -> None:
        self._connect_btn.setEnabled(self._connect_allowed(self._session.state))

    def _connect_allowed(self, state: SessionState) -> bool:
        if state != SessionState.DISCONNECTED:
            return False
        probe = self._probe_box.currentData()
        if probe is None:
            return False  # the "No probes found" sentinel has no probe to connect to
        if probe.backend == "pyocd":
            return bool(self._chosen_target)  # a pyOCD target must be chosen first
        return True

    def _on_attach(self) -> None:
        item = self._targets.currentItem()
        if item is None and self._targets.count() > 0:
            item = self._targets.item(0)
        if item is not None:
            self._session.attach(int(item.data(Qt.ItemDataRole.UserRole)))

    def _on_target_double_clicked(self, item) -> None:
        # Double-clicking a target row attaches to it (when connected and not mid-operation).
        if self._session.state in (SessionState.CONNECTED, SessionState.ATTACHED):
            self._session.attach(int(item.data(Qt.ItemDataRole.UserRole)))

    def _browse_gdb(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr("Select GDB executable"))
        if path:
            self._gdb_path.setText(path)

    def _autodetect_gdb(self) -> None:
        info = find_gdb(self._gdb_path.text().strip() or None)
        if info is not None:
            self._gdb_path.setText(info.path)
            self._status.setText(self.tr("Found: %s") % info.version)
        else:
            self._status.setText(self.tr("No GDB found on PATH"))

    # --- reactions to session --------------------------------------------------

    def _populate_targets(self, targets) -> None:
        self._targets.clear()
        for target in targets:
            item = QListWidgetItem(f"{target.index}: {target.name}")
            item.setData(Qt.ItemDataRole.UserRole, target.index)
            self._targets.addItem(item)
        if self._targets.count():
            self._targets.setCurrentRow(0)
        if self._pending_autoattach and targets:
            # pyOCD connect: attach to its single target without a manual step.
            self._pending_autoattach = False
            self._session.attach(targets[0].index)

    def _on_operation_failed(self, op: str, _message: str) -> None:
        # pyOCD connects+attaches in one Connect click and has no manual Attach control. If the
        # attach fails (e.g. the target was unplugged from the probe), the session is left
        # "connected, no target" with Connect disabled. Roll back to DISCONNECTED so the user can
        # just click Connect again to retry, instead of having to press Disconnect first.
        probe = self._probe_box.currentData()
        if op == "attach" and probe is not None and probe.backend == "pyocd":
            self._session.disconnect_probe()

    def _apply_capabilities(self, caps) -> None:
        self._tpwr.setEnabled(caps.supports_target_power)
        self._cur.setEnabled(caps.supports_connect_under_reset)

    def _update_for_state(self, state: SessionState) -> None:
        connected = state in (SessionState.CONNECTED, SessionState.ATTACHED)
        idle = state != SessionState.BUSY and state != SessionState.CONNECTING
        self._connect_btn.setEnabled(self._connect_allowed(state))
        self._disconnect_btn.setEnabled(connected and idle)
        self._scan_btn.setEnabled(connected and idle)
        self._attach_btn.setEnabled(connected and idle)
        self._probe_box.setEnabled(state == SessionState.DISCONNECTED)
        # Picking a target fetches pyOCD's catalog, which clobbers the global current-session; only
        # allow it while DISCONNECTED so it can't disturb a live session.
        self._choose_target_btn.setEnabled(state == SessionState.DISCONNECTED)
        self._status.setText(self.tr("State: %s") % state.value)

        # Scan targets automatically right after a successful connect.
        if state is SessionState.CONNECTED and self._prev_state is SessionState.CONNECTING:
            self._session.scan()
        self._prev_state = state

    def _persist(self) -> None:
        self._settings.gdb_path = self._gdb_path.text().strip()
        self._settings.port_override = self._port_override.text().strip()
        self._settings.target_power = self._tpwr.isChecked()
        self._settings.tpwr_delay_ms = self._tpwr_delay.value()
        self._settings.connect_under_reset = self._cur.isChecked()
        self._settings.interface_speed_hz = self._speed_hz()
        probe = self._probe_box.currentData()
        if probe is not None:
            self._settings.last_probe_serial = probe.serial


def _checkbox(label: str, checked: bool):
    from PySide6.QtWidgets import QCheckBox

    box = QCheckBox(label)
    box.setChecked(checked)
    return box


def _wrap(layout) -> QWidget:
    widget = QWidget()
    layout.setContentsMargins(0, 0, 0, 0)
    widget.setLayout(layout)
    return widget
