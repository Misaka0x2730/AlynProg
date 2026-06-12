"""The Black Magic Probe backend: a :class:`ProbeBackend` implemented over a GDB/MI session.

This is implementation #1 of the probe abstraction. It owns a :class:`GdbMiSession`, detects the
firmware's monitor dialect on connect, and maps the abstract operations to GDB/MI commands and BMP
``monitor`` commands. The session is injectable so tests can drive a fake-gdb subprocess.
"""

from __future__ import annotations

import contextlib
import re
import tempfile
import threading
import time
from pathlib import Path

from intelhex import IntelHex

from alynprog.backends.bmp.dialect import BmpDialect
from alynprog.backends.bmp.discovery import discover_bmp_probes
from alynprog.core.backend import (
    CompareResult,
    ConnectOptions,
    EraseKind,
    EraseSpec,
    MemoryRegion,
    ProbeBackend,
    ProbeCapabilities,
    ProbeInfo,
    ProgramResult,
    ProgressCallback,
    RegionKind,
    TargetInfo,
    VerifyMethod,
)
from alynprog.core.errors import GdbError, NotSupportedError, ProbeError, TransportError
from alynprog.core.image import FirmwareImage
from alynprog.transports.dialects import DialectFeatures
from alynprog.transports.gdbmi import (
    GdbMiSession,
    parse_compare_sections,
    parse_download_progress,
    parse_info_mem,
    parse_memory_bytes,
    parse_register_names,
    parse_register_values,
)

READ_CHUNK = 1024
WRITE_CHUNK = 1024

CONNECT_TIMEOUT = 20.0
SCAN_TIMEOUT = 20.0
ATTACH_TIMEOUT = 20.0
PROGRAM_TIMEOUT = 300.0
ERASE_TIMEOUT = 120.0
VERIFY_TIMEOUT = 120.0
HALT_TIMEOUT = 5.0

_TARGET_RE = re.compile(r"^\s*(\d+)\s+\*?\s*(.+?)\s*$")

_SETUP_COMMANDS = (
    "-gdb-set confirm off",
    "-gdb-set pagination off",
    "-gdb-set mi-async on",
    "-gdb-set mem inaccessible-by-default off",
)


class BmpBackend(ProbeBackend):
    name = "bmp"

    def __init__(
        self,
        *,
        gdb_path: str = "",
        port: str = "",
        log_callback=None,
        session: GdbMiSession | None = None,
    ) -> None:
        self._gdb_path = gdb_path
        self._port = port
        self._log_callback = log_callback
        self._session = session
        self._dialect: BmpDialect | None = None
        self._version_text = ""
        self._options = ConnectOptions()
        self._capabilities = _default_capabilities()
        # Capabilities while connected but not attached (restored on detach).
        self._connected_capabilities = _default_capabilities()
        self._memory_map: list[MemoryRegion] = []
        self._attached = False
        self._running = False  # True while the core is executing (resumed)
        self._last_download_total = 0

    # --- discovery -------------------------------------------------------------

    @classmethod
    def create(cls, *, gdb_path: str = "", log_callback=None) -> BmpBackend:
        return cls(gdb_path=gdb_path, log_callback=log_callback)

    @classmethod
    def discover(cls) -> list[ProbeInfo]:
        return discover_bmp_probes()

    # --- connection ------------------------------------------------------------

    def connect(self, probe: ProbeInfo, options: ConnectOptions) -> None:
        self._options = options
        if probe.identifier:
            self._port = probe.identifier
        if self._session is None:
            if not self._gdb_path:
                raise ProbeError("no GDB executable configured")
            self._session = GdbMiSession(self._gdb_path, log_callback=self._log_callback)
        self._session.start()
        # Track run state from async records so _ensure_halted only interrupts a genuinely running
        # core (some firmware/probes accept an interrupt when already stopped without reporting a
        # stop, which would otherwise stall).
        self._session.on_async("running", self._on_running)
        self._session.on_async("stopped", self._on_stopped)
        for command in _SETUP_COMMANDS:
            self._session.execute(command)
        self._session.execute(
            f"-target-select extended-remote {self._port}", timeout=CONNECT_TIMEOUT
        )
        self._detect_dialect()
        self._apply_connect_options()
        self._capabilities = self._build_capabilities()
        self._connected_capabilities = self._capabilities

    def _detect_dialect(self) -> None:
        version, _ = self._session.console("monitor version")
        self._version_text = version
        help_text, _ = self._session.console("monitor help")
        self._dialect = BmpDialect.detect(version, help_text)

    def _refine_dialect_after_attach(self) -> None:
        # BMP registers target-specific commands (erase_mass, erase_range, option, …) only after
        # attaching, so re-read 'monitor help' here to pick them up and rebuild capabilities.
        help_text, _ = self._session.console("monitor help")
        self._dialect = BmpDialect.detect(self._version_text, help_text)
        self._capabilities = self._build_capabilities()

    def _apply_connect_options(self) -> None:
        assert self._dialect is not None
        # Set the SWD/JTAG interface clock first.
        freq_command = self._dialect.frequency_command(self._options.interface_speed_hz)
        if freq_command:
            self._session.console(f"monitor {freq_command}")
        if self._options.connect_under_reset:
            command = self._dialect.connect_under_reset_command(True)
            if command:
                self._session.console(f"monitor {command}")
        if self._options.target_power:
            command = self._dialect.target_power_command(True)
            if command:
                self._session.console(f"monitor {command}")
                # Give the target time to boot before scanning (incl. the auto-scan on connect).
                if self._options.tpwr_delay_ms > 0:
                    time.sleep(self._options.tpwr_delay_ms / 1000.0)

    # --- capabilities ----------------------------------------------------------

    def capabilities(self) -> ProbeCapabilities:
        return self._capabilities

    def _build_capabilities(self) -> ProbeCapabilities:
        feats = self._dialect.features() if self._dialect else DialectFeatures()
        return ProbeCapabilities(
            architectures=frozenset({"cortex-m"}),
            supports_mass_erase=feats.has_mass_erase,
            supports_sector_erase=feats.has_range_erase,
            supports_target_power=feats.has_target_power,
            supports_connect_under_reset=feats.has_connect_under_reset,
            supports_run_control=True,
            supports_register_access=True,
            needs_external_binary=True,
            verify_method=VerifyMethod.COMPARE_SECTIONS,
        )

    # --- targets ---------------------------------------------------------------

    def scan_targets(self) -> list[TargetInfo]:
        session = self._require_session()
        command = self._require_dialect().scan_command(self._options.transport)
        text, _ = session.console(f"monitor {command}", timeout=SCAN_TIMEOUT)
        return _parse_targets(text)

    def attach(self, index: int) -> None:
        session = self._require_session()
        session.console(f"attach {index}", timeout=ATTACH_TIMEOUT)
        self._attached = True
        self._running = False  # attach halts the core
        self._memory_map = self._read_memory_map()
        self._refine_dialect_after_attach()

    def _read_memory_map(self) -> list[MemoryRegion]:
        text, _ = self._require_session().console("info mem")
        regions: list[MemoryRegion] = []
        for raw in parse_info_mem(text):
            regions.append(
                MemoryRegion(raw.start, raw.size, RegionKind(raw.kind), blocksize=raw.blocksize)
            )
        return regions

    def memory_map(self) -> list[MemoryRegion]:
        return list(self._memory_map)

    # --- memory ----------------------------------------------------------------

    def read_memory(self, addr: int, size: int) -> bytes:
        session = self._require_session()
        out = bytearray()
        cur = addr
        remaining = size
        while remaining > 0:
            count = min(remaining, READ_CHUNK)
            result = session.execute(f"-data-read-memory-bytes 0x{cur:x} {count}")
            chunk = parse_memory_bytes(result.payload)
            if not chunk:
                break
            out += chunk
            cur += len(chunk)
            remaining -= len(chunk)
            if len(chunk) < count:
                break
        return bytes(out)

    def _write_raw_memory(self, addr: int, data: bytes) -> None:
        # Direct (RAM-style) write. GDB rejects this for flash; flash goes through the
        # read-modify-write path in ProbeBackend.write_memory -> _program_flash_block instead.
        session = self._require_session()
        cur = addr
        index = 0
        while index < len(data):
            chunk = data[index : index + WRITE_CHUNK]
            session.execute(f"-data-write-memory-bytes 0x{cur:x} {chunk.hex()}")
            cur += len(chunk)
            index += len(chunk)

    def _program_flash_block(self, addr: int, data: bytes) -> None:
        # Program a full (already read-modified) sector by loading it as an Intel-HEX image: GDB's
        # flash protocol (vFlashErase/vFlashWrite) erases the block and writes the new contents.
        session = self._require_session()
        with tempfile.TemporaryDirectory(prefix="alynprog-rmw-") as tmp:
            path = Path(tmp) / "block.hex"
            ihex = IntelHex()
            ihex.frombytes(bytes(data), offset=addr)
            ihex.write_hex_file(str(path))
            session.execute(f'-file-exec-and-symbols "{path}"')
            session.execute("-target-download", timeout=PROGRAM_TIMEOUT)

    # --- programming -----------------------------------------------------------

    def program(
        self,
        image: FirmwareImage,
        progress: ProgressCallback | None = None,
        *,
        verify: bool = True,
        run_after: bool = False,
    ) -> ProgramResult:
        session = self._require_session()
        self._last_download_total = 0
        with tempfile.TemporaryDirectory(prefix="alynprog-") as tmp:
            path = image.loadable_path(Path(tmp))
            session.execute(f'-file-exec-and-symbols "{path}"')
            handler = self._make_download_handler(progress)
            session.on_async("download", handler)
            try:
                session.execute("-target-download", timeout=PROGRAM_TIMEOUT)
            finally:
                session.remove_async("download", handler)

        sections: tuple[CompareResult, ...] = ()
        verified: bool | None = None
        if verify:
            text, _ = session.console("compare-sections", timeout=VERIFY_TIMEOUT)
            sections = tuple(CompareResult(s.name, s.matched) for s in parse_compare_sections(text))
            verified = all(s.matched for s in sections) if sections else None

        if run_after:
            # 'monitor reset' resets and runs the target — and detaches the session on BMP.
            self.reset()
        # When not running after, leave the target attached and halted at the loaded entry point
        # (no 'monitor reset', which would detach and force a re-scan/attach to continue).
        written = image.total_size or self._last_download_total
        return ProgramResult(bytes_written=written, verified=verified, sections=sections)

    def _make_download_handler(self, progress: ProgressCallback | None):
        def handler(_message: str, payload) -> None:
            prog = parse_download_progress(payload)
            if prog.total_size:
                self._last_download_total = prog.total_size
            if progress and prog.total_sent is not None and prog.total_size:
                progress(prog.total_sent, prog.total_size)

        return handler

    # --- erase / reset ---------------------------------------------------------

    def erase(self, spec: EraseSpec) -> None:
        session = self._require_session()
        dialect = self._require_dialect()
        if spec.kind is EraseKind.MASS:
            command = dialect.mass_erase_command()
            if command is None:
                raise NotSupportedError("mass erase is not supported by this probe/target")
        else:
            command = dialect.range_erase_command(spec.addr, spec.length)
            if command is None:
                raise NotSupportedError("range erase is not supported by this probe/target")
        session.console(f"monitor {command}", timeout=ERASE_TIMEOUT)

    def reset(self, *, halt: bool = False) -> None:
        # On BMP, 'monitor reset' pulses nRST: it resets AND runs the target, and detaches the debug
        # session. There is no "reset and stay halted" via monitor reset, so `halt` cannot be
        # honoured here; the session is left detached (callers transition the UI accordingly).
        session = self._require_session()
        with contextlib.suppress(TransportError):
            session.console(f"monitor {self._require_dialect().reset_command()}")
        self._mark_detached()

    def is_attached(self) -> bool:
        return self._attached

    def _mark_detached(self) -> None:
        self._attached = False
        self._running = True  # target is running after the reset pulse
        self._memory_map = []
        self._capabilities = self._connected_capabilities

    def _on_running(self, _message, _payload) -> None:
        self._running = True

    def _on_stopped(self, _message, _payload) -> None:
        self._running = False

    def _ensure_halted(self) -> None:
        # Required before erasing/programming flash (e.g. memory-view edits after the core was
        # resumed). Interrupt the core if running and wait for the actual stop before proceeding.
        if not self._running:
            return
        session = self._require_session()
        stopped = threading.Event()

        def on_stopped(_message, _payload) -> None:
            stopped.set()

        session.on_async("stopped", on_stopped)
        try:
            try:
                session.execute("-exec-interrupt")
            except GdbError:
                # Already stopped: GDB rejects an interrupt when the target isn't running.
                self._running = False
                return
            stopped.wait(HALT_TIMEOUT)
        finally:
            session.remove_async("stopped", on_stopped)
        self._running = False

    def raw_monitor(self, command: str) -> str:
        text, _ = self._require_session().console(f"monitor {command}")
        return text

    # --- registers (transport-ready; UI exposes this in a later milestone) -----

    def read_registers(self) -> dict[str, int]:
        session = self._require_session()
        names = parse_register_names(session.execute("-data-list-register-names").payload)
        values = parse_register_values(session.execute("-data-list-register-values x").payload)
        out: dict[str, int] = {}
        for index, name in names.items():
            raw = values.get(index)
            parsed = _parse_int(raw)
            if parsed is not None:
                out[name] = parsed
        return out

    def write_register(self, name: str, value: int) -> None:
        session = self._require_session()
        session.execute(f'-data-evaluate-expression "${name}={value}"')

    # --- teardown --------------------------------------------------------------

    def detach(self) -> None:
        if self._session is not None and self._attached:
            with contextlib.suppress(TransportError):
                self._session.console("detach")
        self._attached = False

    def close(self) -> None:
        if self._session is not None:
            self._session.stop()
            self._session = None
        self._attached = False

    # --- helpers ---------------------------------------------------------------

    def _require_session(self) -> GdbMiSession:
        if self._session is None or not self._session.is_alive():
            raise ProbeError("not connected")
        return self._session

    def _require_dialect(self) -> BmpDialect:
        if self._dialect is None:
            raise ProbeError("not connected")
        return self._dialect


def _default_capabilities() -> ProbeCapabilities:
    return ProbeCapabilities(needs_external_binary=True)


def _parse_targets(text: str) -> list[TargetInfo]:
    targets: list[TargetInfo] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("No.") or "Available Targets" in stripped:
            continue
        match = _TARGET_RE.match(line)
        if match:
            targets.append(TargetInfo(index=int(match.group(1)), name=match.group(2).strip()))
    return targets


def _parse_int(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(raw, 16) if raw.lower().startswith("0x") else int(raw)
    except ValueError:
        return None
