"""SessionController: the UI-facing facade over the backend worker thread.

It owns the :class:`QThread` + :class:`BackendWorker`, exposes request methods the UI calls on the
GUI thread, and re-emits the worker's results as typed signals while maintaining a small session
state machine (Disconnected → Connecting → Connected → Attached, with a transient Busy during
explicit operations). Memory page reads deliberately do *not* flip the state to Busy so the hex
view stays interactive while scrolling.
"""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import QObject, Qt, QThread, Signal

from alynprog.core.backend import ConnectOptions, EraseSpec, ProbeInfo
from alynprog.core.worker import BackendWorker


class SessionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ATTACHED = "attached"
    BUSY = "busy"


class SessionController(QObject):
    # Public, UI-facing signals.
    state_changed = Signal(object)  # SessionState
    log_line = Signal(str, str)  # (stream, text)
    targets_found = Signal(object)  # list[TargetInfo]
    capabilities_changed = Signal(object)  # ProbeCapabilities
    memory_map_changed = Signal(object)  # list[MemoryRegion]
    memory_page_ready = Signal(int, object)  # (base, bytes)
    memory_written = Signal(int, int, bool, str)  # (addr, length, ok, error)
    registers_ready = Signal(object)  # dict[str, int]
    progress = Signal(str, int, int)  # (op, done, total)
    program_done = Signal(object)  # ProgramResult
    compare_done = Signal(object)  # MemoryCompareResult
    operation_failed = Signal(str, str)  # (op, message)
    operation_succeeded = Signal(str)  # op

    # Internal: marshals a request to the worker thread.
    _request = Signal(str, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._state = SessionState.DISCONNECTED
        self._base_state = SessionState.DISCONNECTED
        self._capabilities = None
        self._memory_map: list = []

        self._thread = QThread()
        self._thread.setObjectName("alynprog-backend")
        self._worker = BackendWorker()
        self._worker.moveToThread(self._thread)

        self._request.connect(self._worker.dispatch, Qt.ConnectionType.QueuedConnection)
        self._worker.log_line.connect(self.log_line)
        self._worker.connected.connect(self._on_connected)
        self._worker.targets_found.connect(self._on_targets)
        self._worker.attached.connect(self._on_attached)
        self._worker.page_ready.connect(self.memory_page_ready)
        self._worker.write_done.connect(self.memory_written)
        self._worker.registers_ready.connect(self.registers_ready)
        self._worker.progress.connect(self.progress)
        self._worker.program_done.connect(self.program_done)
        self._worker.compare_done.connect(self.compare_done)
        self._worker.detached.connect(self._on_detached)
        self._worker.succeeded.connect(self._on_succeeded)
        self._worker.failed.connect(self._on_failed)
        self._worker.disconnected.connect(self._on_disconnected)

        self._thread.start()

    # --- state -----------------------------------------------------------------

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def capabilities(self):
        return self._capabilities

    @property
    def memory_map(self) -> list:
        """The attached target's last-known memory map (empty when not attached)."""
        return self._memory_map

    def _set_state(self, state: SessionState) -> None:
        if state != self._state:
            self._state = state
            self.state_changed.emit(state)

    # --- requests (called on the GUI thread) -----------------------------------

    def connect_probe(
        self,
        backend_name: str,
        probe: ProbeInfo,
        options: ConnectOptions,
        gdb_path: str = "",
    ) -> None:
        self._set_state(SessionState.CONNECTING)
        self._request.emit("connect", (backend_name, probe, options, gdb_path))

    def scan(self) -> None:
        self._set_state(SessionState.BUSY)
        self._request.emit("scan", ())

    def attach(self, index: int) -> None:
        self._set_state(SessionState.BUSY)
        self._request.emit("attach", (index,))

    def read_page(self, base: int, size: int) -> None:
        # No state change: page reads run concurrently with an interactive UI.
        self._request.emit("read_page", (base, size))

    def write_bytes(self, addr: int, data: bytes) -> None:
        self._request.emit("write", (addr, bytes(data)))

    def program(self, path: str, base: int | None, *, verify: bool, run_after: bool) -> None:
        self._set_state(SessionState.BUSY)
        self._request.emit("program", (path, base, verify, run_after))

    def erase(self, spec: EraseSpec) -> None:
        self._set_state(SessionState.BUSY)
        self._request.emit("erase", (spec,))

    def reset(self, *, halt: bool = False) -> None:
        self._set_state(SessionState.BUSY)
        self._request.emit("reset", (halt,))

    def read_registers(self) -> None:
        self._request.emit("registers", ())

    def save_range(self, addr: int, length: int, path: str) -> None:
        self._set_state(SessionState.BUSY)
        self._request.emit("save_range", (addr, length, path))

    def compare(self, segments: list[tuple[int, bytes]]) -> None:
        self._set_state(SessionState.BUSY)
        self._request.emit("compare", (segments,))

    def cancel_compare(self) -> None:
        """Ask a running comparison to stop (the worker checks between read chunks)."""
        self._worker.request_cancel()

    def disconnect_probe(self) -> None:
        self._set_state(SessionState.BUSY)
        self._request.emit("disconnect", ())

    # --- result relays ---------------------------------------------------------

    def _on_connected(self, capabilities) -> None:
        self._capabilities = capabilities
        self._base_state = SessionState.CONNECTED
        self._set_state(SessionState.CONNECTED)
        self.capabilities_changed.emit(capabilities)

    def _on_targets(self, targets) -> None:
        self._set_state(self._base_state)
        self.targets_found.emit(targets)

    def _on_attached(self, memory_map, capabilities) -> None:
        self._capabilities = capabilities
        self._memory_map = list(memory_map)
        self._base_state = SessionState.ATTACHED
        self._set_state(SessionState.ATTACHED)
        self.capabilities_changed.emit(capabilities)
        self.memory_map_changed.emit(memory_map)

    def _on_detached(self, capabilities) -> None:
        # The target was detached (e.g. reset+run after programming) but the probe is still
        # connected: return to the "connected, before scan" state and clear stale target/memory UI.
        self._capabilities = capabilities
        self._memory_map = []
        self._base_state = SessionState.CONNECTED
        self._set_state(SessionState.CONNECTED)
        self.capabilities_changed.emit(capabilities)
        self.targets_found.emit([])
        self.memory_map_changed.emit([])

    def _on_succeeded(self, op: str) -> None:
        self._set_state(self._base_state)
        self.operation_succeeded.emit(op)

    def _on_failed(self, op: str, message: str) -> None:
        if op == "connect":
            self._base_state = SessionState.DISCONNECTED
        self._set_state(self._base_state)
        self.operation_failed.emit(op, message)

    def _on_disconnected(self) -> None:
        self._capabilities = None
        self._memory_map = []
        self._base_state = SessionState.DISCONNECTED
        self._set_state(SessionState.DISCONNECTED)
        # Clear stale target/memory UI left over from the previous connection.
        self.targets_found.emit([])
        self.memory_map_changed.emit([])

    # --- teardown --------------------------------------------------------------

    def shutdown(self) -> None:
        """Close the backend and stop the worker thread. Safe to call once, at app exit."""
        if self._thread.isRunning():
            # Run the worker's cleanup synchronously, then stop the event loop.
            from PySide6.QtCore import QMetaObject

            QMetaObject.invokeMethod(
                self._worker, "shutdown", Qt.ConnectionType.BlockingQueuedConnection
            )
            self._thread.quit()
            self._thread.wait(5000)
