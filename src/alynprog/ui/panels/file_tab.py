"""A firmware-file editor tab: a hex pane over an editable :class:`FirmwareDocument`.

The tab reuses :class:`HexPane` for the table and toolbar, makes every byte editable, and adds
Save / Save As plus Compare. It owns the document's dirty lifecycle: edits mark it dirty (reflected
in the tab title via :attr:`dirtyChanged`), Save writes back in place where the format allows it,
and Save As exports to HEX or BIN. ELF documents have no in-place save, so their Save falls through
to Save As.

Compare reads the device memory the file would occupy and, when the read finishes, emits
:attr:`compareReady` so the work area can open the side-by-side result tab. A raw ``.bin`` has no
addresses, so the user is asked for a start address first; HEX and ELF already carry their own.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFileDialog, QMessageBox, QProgressDialog, QPushButton, QWidget

from alynprog.core.compare import MemoryCompareResult
from alynprog.core.document import FirmwareDocument
from alynprog.core.errors import ImageError
from alynprog.core.image import ImageKind
from alynprog.core.session import SessionController, SessionState
from alynprog.core.settings import Settings
from alynprog.ui.panels.base_address_dialog import BaseAddressDialog
from alynprog.ui.panels.hexview.sources import FileDocumentSource
from alynprog.ui.panels.hexview.view import HexPane

_HEX_FILTER = "Intel HEX (*.hex)"
_BIN_FILTER = "Binary (*.bin)"
_DEFAULT_BASE = 0x0800_0000


class FileTab(HexPane):
    dirtyChanged = Signal(bool)  # emitted when the dirty marker or filename may have changed
    compareReady = Signal(object, str)  # (MemoryCompareResult, file_label) — open the result tab

    def __init__(
        self,
        document: FirmwareDocument,
        session: SessionController,
        settings: Settings | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(settings, parent)
        self._document = document
        self._session = session
        self._source = FileDocumentSource(document, self)
        self._bind_source(self._source.request_page, self._source.write)
        self._source.page_ready.connect(self._model.fill_page)
        self._source.write_done.connect(self._on_write_done)
        self._source.changed.connect(self._on_document_changed)
        self._model.set_editable_override(True)

        self._comparing = False
        self._progress_dlg: QProgressDialog | None = None
        # Seed from the session in case we are opened while a target is already attached (the
        # initial memory_map_changed would have fired before we could subscribe).
        self._memory_map: list = list(session.memory_map)

        self._populate_regions(document.regions())

        self._session.memory_map_changed.connect(self._on_memory_map)
        self._session.progress.connect(self._on_progress)
        self._session.compare_done.connect(self._on_compare_done)
        self._session.operation_failed.connect(self._on_failed)
        self._session.state_changed.connect(lambda _state: self._update_compare_enabled())

        self._update_actions()
        self._update_compare_enabled()

    # --- public surface --------------------------------------------------------

    @property
    def document(self) -> FirmwareDocument:
        return self._document

    def title(self) -> str:
        """Tab label: the filename, with a trailing ``*`` while there are unsaved edits."""
        return f"{self._document.path.name}{'*' if self._document.dirty else ''}"

    def maybe_save(self) -> bool:
        """Prompt about unsaved edits when closing. Returns True if the tab may be closed."""
        if not self._document.dirty:
            return True
        choice = QMessageBox.question(
            self,
            self.tr("Unsaved changes"),
            self.tr("Save changes to %s before closing?") % self._document.path.name,
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if choice == QMessageBox.StandardButton.Save:
            return self.save()
        return choice == QMessageBox.StandardButton.Discard

    def save(self) -> bool:
        if not self._document.supports_in_place_save:
            return self.save_as()
        try:
            self._document.save()
        except (OSError, ImageError) as exc:
            self.logMessage.emit("error", self.tr("Save failed: %s") % exc)
            return False
        self.logMessage.emit("info", self.tr("Saved %s") % self._document.path.name)
        self._on_document_changed()
        return True

    def save_as(self) -> bool:
        default = _BIN_FILTER if self._document.kind is ImageKind.BIN else _HEX_FILTER
        filters = f"{_HEX_FILTER};;{_BIN_FILTER}"
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Save firmware as"), str(self._document.path), filters, default
        )
        if not path:
            return False
        try:
            self._document.save_as(path)
        except (OSError, ImageError) as exc:
            self.logMessage.emit("error", self.tr("Save failed: %s") % exc)
            return False
        if self._settings is not None:
            self._settings.push_recent_file(str(self._document.path))
        self.logMessage.emit("info", self.tr("Saved %s") % self._document.path.name)
        self._on_document_changed()
        return True

    # --- toolbar / save reactions ----------------------------------------------

    def _toolbar_extras(self) -> list[QWidget]:
        self._save_btn = QPushButton(self.tr("Save"), self)
        self._save_btn.clicked.connect(self.save)
        self._save_as_btn = QPushButton(self.tr("Save As…"), self)
        self._save_as_btn.clicked.connect(self.save_as)
        self._compare_btn = QPushButton(self.tr("Compare…"), self)
        self._compare_btn.clicked.connect(self.compare)
        return [self._save_btn, self._save_as_btn, self._compare_btn]

    def _on_document_changed(self) -> None:
        self._update_actions()
        self.dirtyChanged.emit(self._document.dirty)

    def _update_actions(self) -> None:
        self._save_btn.setEnabled(self._document.dirty and self._document.supports_in_place_save)

    def _on_write_done(self, addr: int, length: int, ok: bool, error: str) -> None:
        self._model.invalidate(addr, length)
        if not ok:
            self.logMessage.emit(
                "warn", self.tr("Cannot edit 0x%08X: %s") % (addr, error or "read-only")
            )

    # --- compare ---------------------------------------------------------------

    def _update_compare_enabled(self) -> None:
        attached = self._session.state is SessionState.ATTACHED
        self._compare_btn.setEnabled(attached)
        self._compare_btn.setToolTip("" if attached else self.tr("Attach to a device to compare"))

    def _on_memory_map(self, regions) -> None:
        self._memory_map = list(regions)

    def compare(self) -> None:
        if self._session.state is not SessionState.ATTACHED:
            self.logMessage.emit("warn", self.tr("Attach to a device before comparing"))
            return
        file_segments = self._document.segment_views()
        if not file_segments:
            return
        delta = 0
        if self._document.kind is ImageKind.BIN:
            base = self._ask_base_address()
            if base is None:
                return
            delta = base  # a .bin's single segment sits at file offset 0
        device_segments = [(addr + delta, data) for addr, data in file_segments]
        # Compare only what the file and device memory have in common: clamp each segment to the
        # device memory map. A file larger or smaller than the mapped memory just compares the
        # overlapping bytes — no prompt either way.
        device_segments = self._clamp_to_map(device_segments)
        if not device_segments:
            self.logMessage.emit(
                "warn", self.tr("Nothing to compare: the file does not overlap device memory")
            )
            return
        self._comparing = True
        self.logMessage.emit("info", self.tr("Comparing %s with device memory…") % self.title())
        self._show_progress()
        self._session.compare(device_segments)

    def _show_progress(self) -> None:
        dlg = QProgressDialog(
            self.tr("Comparing with device memory…"), self.tr("Cancel"), 0, 100, self
        )
        dlg.setWindowTitle(self.tr("Compare"))
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setMinimumDuration(0)
        dlg.canceled.connect(self._cancel_compare)
        dlg.setValue(0)
        self._progress_dlg = dlg

    def _close_progress(self) -> None:
        if self._progress_dlg is not None:
            self._progress_dlg.close()
            self._progress_dlg = None

    def _cancel_compare(self) -> None:
        # The worker stops at the next read chunk and reports failure ("compare cancelled").
        self._session.cancel_compare()

    def _on_progress(self, op: str, done: int, total: int) -> None:
        dlg = self._progress_dlg
        if (
            op == "compare"
            and self._comparing
            and dlg is not None
            and not dlg.wasCanceled()
            and total
        ):
            dlg.setValue(int(done * 100 / total))

    def _ask_base_address(self) -> int | None:
        default = self._settings.last_bin_base_addr if self._settings is not None else _DEFAULT_BASE
        dialog = BaseAddressDialog(default, self)
        if dialog.exec() != BaseAddressDialog.DialogCode.Accepted:
            return None
        addr = dialog.base_address()
        if addr is None:
            self.logMessage.emit("error", self.tr("Invalid base address"))
            return None
        if self._settings is not None:
            self._settings.last_bin_base_addr = addr
        return addr

    def _clamp_to_map(self, segments: list[tuple[int, bytes]]) -> list[tuple[int, bytes]]:
        """Trim each segment to the parts that fall within a device memory region.

        A segment that overflows a region's end keeps only the bytes inside it; one that lies
        entirely outside the map contributes nothing. This is how "compare the smaller size" is
        realised when the file and the mapped memory differ in extent.
        """
        clamped: list[tuple[int, bytes]] = []
        for addr, data in segments:
            end = addr + len(data)
            for region in self._memory_map:
                lo = max(addr, region.start)
                hi = min(end, region.end)
                if lo < hi:
                    clamped.append((lo, data[lo - addr : hi - addr]))
        return clamped

    def _on_compare_done(self, result: MemoryCompareResult) -> None:
        if not self._comparing:
            return
        self._comparing = False
        self._close_progress()
        if result.matched:
            summary = self.tr("Contents match device memory (%d bytes)") % result.bytes_compared
        else:
            summary = self.tr("%d byte(s) differ in %d range(s)") % (
                result.bytes_differing,
                result.range_count,
            )
        self.logMessage.emit("info", summary)
        self.compareReady.emit(result, self._document.path.name)

    def _on_failed(self, op: str, message: str) -> None:
        if op == "compare" and self._comparing:
            self._comparing = False  # the error (or cancellation) is logged via operation_failed
            self._close_progress()
