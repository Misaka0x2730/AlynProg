"""The backend worker that runs all blocking probe operations off the GUI thread.

A single :class:`BackendWorker` is moved onto a dedicated :class:`QThread` by
:class:`~alynprog.core.session.SessionController`. Requests arrive as queued ``dispatch`` calls and
are therefore serialized (GDB/MI is serial anyway); results are reported only through typed signals
— the worker never touches widgets. The backend's own log callback also funnels into a signal, so
GDB/MI traffic from the transport's reader thread reaches the UI safely.
"""

from __future__ import annotations

import contextlib

from PySide6.QtCore import QObject, Signal, Slot

from alynprog.core.error_messages import friendly_error
from alynprog.core.image import FirmwareImage
from alynprog.core.registry import backend_class


class BackendWorker(QObject):
    log_line = Signal(str, str)  # (stream, text)
    connected = Signal(object)  # ProbeCapabilities
    targets_found = Signal(object)  # list[TargetInfo]
    attached = Signal(object, object)  # (memory_map, ProbeCapabilities)
    page_ready = Signal(int, object)  # (base_addr, bytes)
    write_done = Signal(int, int, bool, str)  # (addr, length, ok, error)
    registers_ready = Signal(object)  # dict[str, int]
    progress = Signal(str, int, int)  # (op, done, total)
    program_done = Signal(object)  # ProgramResult
    detached = Signal(object)  # ProbeCapabilities — target detached (reset+run) but probe connected
    succeeded = Signal(str)  # op
    failed = Signal(str, str)  # (op, message)
    disconnected = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._backend = None

    # --- dispatch --------------------------------------------------------------

    @Slot(str, object)
    def dispatch(self, method: str, args: tuple) -> None:
        handler = getattr(self, f"_do_{method}", None)
        if handler is None:
            self.failed.emit(method, f"unknown request: {method}")
            return
        try:
            handler(*args)
        except Exception as exc:
            self.failed.emit(method, friendly_error(str(exc)))

    @Slot()
    def shutdown(self) -> None:
        self._close_backend()

    # --- handlers --------------------------------------------------------------

    def _do_connect(self, backend_name: str, probe, options, gdb_path: str) -> None:
        cls = backend_class(backend_name)
        self._backend = cls.create(gdb_path=gdb_path, log_callback=self._emit_log)
        self._backend.connect(probe, options)
        self.connected.emit(self._backend.capabilities())

    def _do_scan(self) -> None:
        self.targets_found.emit(self._require().scan_targets())

    def _do_attach(self, index: int) -> None:
        backend = self._require()
        backend.attach(index)
        self.attached.emit(backend.memory_map(), backend.capabilities())

    def _do_read_page(self, base: int, size: int) -> None:
        data = self._require().read_memory(base, size)
        self.page_ready.emit(base, data)

    def _do_write(self, addr: int, data: bytes) -> None:
        backend = self._require()
        ok = True
        error = ""
        try:
            backend.write_memory(addr, data)
            readback = backend.read_memory(addr, len(data))
            ok = readback == data
            if not ok:
                error = "value did not read back after write"
        except Exception as exc:
            ok = False
            error = friendly_error(str(exc))
        self.write_done.emit(addr, len(data), ok, error)

    def _do_program(self, path: str, base: int | None, verify: bool, run_after: bool) -> None:
        image = FirmwareImage.load(path, base)
        result = self._require().program(
            image,
            progress=lambda done, total: self.progress.emit("program", done, total),
            verify=verify,
            run_after=run_after,
        )
        self.program_done.emit(result)
        self._emit_detached_if_needed()
        self.succeeded.emit("program")

    def _do_erase(self, spec) -> None:
        self._require().erase(spec)
        self.succeeded.emit("erase")

    def _do_reset(self, halt: bool) -> None:
        self._require().reset(halt=halt)
        self._emit_detached_if_needed()
        self.succeeded.emit("reset")

    def _emit_detached_if_needed(self) -> None:
        backend = self._require()
        if not backend.is_attached():
            self.detached.emit(backend.capabilities())

    def _do_registers(self) -> None:
        self.registers_ready.emit(self._require().read_registers())

    def _do_save_range(self, addr: int, length: int, path: str) -> None:
        backend = self._require()
        chunk = 4096
        out = bytearray()
        done = 0
        while done < length:
            count = min(chunk, length - done)
            out += backend.read_memory(addr + done, count)
            done += count
            self.progress.emit("save", done, length)
        with open(path, "wb") as fh:
            fh.write(out)
        self.succeeded.emit("save")

    def _do_disconnect(self) -> None:
        self._close_backend()
        self.disconnected.emit()

    # --- helpers ---------------------------------------------------------------

    def _emit_log(self, stream: str, text: str) -> None:
        self.log_line.emit(stream, text)

    def _require(self):
        if self._backend is None:
            raise RuntimeError("not connected")
        return self._backend

    def _close_backend(self) -> None:
        if self._backend is not None:
            with contextlib.suppress(Exception):
                self._backend.detach()
            with contextlib.suppress(Exception):
                self._backend.close()
            self._backend = None
