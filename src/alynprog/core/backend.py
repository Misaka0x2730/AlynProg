"""The probe-agnostic backend contract.

:class:`ProbeBackend` is deliberately *byte- and image-oriented*, not GDB-shaped: the GDB-based
Black Magic Probe backend, the in-process simulated backend, and a future native pyOCD backend all
implement the same interface. The UI enables/disables features by inspecting the declarative
:class:`ProbeCapabilities` returned by a connected backend.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from alynprog.core.errors import NotSupportedError, TargetError

if TYPE_CHECKING:
    from alynprog.core.image import FirmwareImage

# (bytes_done, bytes_total) — total may be 0 if unknown.
ProgressCallback = Callable[[int, int], None]


class RegionKind(StrEnum):
    FLASH = "flash"
    RAM = "ram"
    UNKNOWN = "unknown"


class VerifyMethod(StrEnum):
    NONE = "none"
    COMPARE_SECTIONS = "compare_sections"
    READBACK = "readback"


@dataclass(frozen=True)
class MemoryRegion:
    start: int
    size: int
    kind: RegionKind = RegionKind.UNKNOWN
    name: str = ""
    blocksize: int = 0  # flash erase-block (sector) size; 0 if unknown/not flash

    @property
    def end(self) -> int:
        return self.start + self.size

    def contains(self, addr: int) -> bool:
        return self.start <= addr < self.end

    def overlaps(self, addr: int, length: int) -> bool:
        return addr < self.end and (addr + length) > self.start


@dataclass(frozen=True)
class ProbeInfo:
    """Identifies a discovered probe before connection."""

    backend: str
    identifier: str  # opaque, backend-specific (e.g. serial port device path)
    display_name: str
    serial: str = ""
    # True when the backend could not unambiguously pick the control port (e.g. macOS device-name
    # ordering) and the user may need to override it manually.
    port_ambiguous: bool = False


@dataclass(frozen=True)
class TargetInfo:
    index: int
    name: str


@dataclass(frozen=True)
class ProbeCapabilities:
    """Declarative feature flags; the UI gates controls on these."""

    architectures: frozenset[str] = frozenset()
    supports_mass_erase: bool = False
    supports_sector_erase: bool = False
    supports_target_power: bool = False
    supports_connect_under_reset: bool = False
    supports_run_control: bool = False
    supports_register_access: bool = False
    needs_external_binary: bool = False
    verify_method: VerifyMethod = VerifyMethod.NONE


@dataclass
class ConnectOptions:
    transport: str = "swd"  # "swd" | "jtag"
    target_power: bool = False
    # Delay (ms) after enabling target power before scanning, to let the target boot.
    tpwr_delay_ms: int = 500
    connect_under_reset: bool = False
    # SWD/JTAG interface clock in Hz (default 1 MHz).
    interface_speed_hz: int = 1_000_000


class EraseKind(StrEnum):
    MASS = "mass"
    RANGE = "range"


@dataclass(frozen=True)
class EraseSpec:
    kind: EraseKind
    addr: int = 0
    length: int = 0


@dataclass(frozen=True)
class CompareResult:
    name: str
    matched: bool


@dataclass(frozen=True)
class ProgramResult:
    bytes_written: int
    verified: bool | None = None  # None when verify was not requested/possible
    sections: tuple[CompareResult, ...] = field(default_factory=tuple)


class ProbeBackend(ABC):
    """Abstract base for all debug-probe backends.

    Concrete subclasses set :attr:`name` and implement the abstract methods. Register-access
    methods have default implementations that raise :class:`NotSupportedError` so that backends
    can opt in incrementally (the v1 BMP backend reports the capability but the registers UI is a
    later milestone).
    """

    name: ClassVar[str] = "abstract"

    @classmethod
    def create(cls, *, gdb_path: str = "", log_callback=None) -> ProbeBackend:
        """Uniform constructor used by the worker. Subclasses needing extra args override this."""
        return cls()

    @classmethod
    @abstractmethod
    def discover(cls) -> list[ProbeInfo]:
        """Enumerate probes of this backend's kind currently attached to the system."""

    @abstractmethod
    def connect(self, probe: ProbeInfo, options: ConnectOptions) -> None:
        """Open the probe and prepare the debug link (does not yet attach to a target)."""

    @abstractmethod
    def capabilities(self) -> ProbeCapabilities:
        """Return current capabilities (refined after :meth:`attach`)."""

    @abstractmethod
    def scan_targets(self) -> list[TargetInfo]:
        """Scan the bus and list available targets."""

    @abstractmethod
    def attach(self, index: int) -> None:
        """Attach to the target with the given 1-based scan index."""

    @abstractmethod
    def memory_map(self) -> list[MemoryRegion]:
        """Return the attached target's memory regions (flash/ram/unknown)."""

    @abstractmethod
    def read_memory(self, addr: int, size: int) -> bytes:
        """Read *size* bytes starting at *addr*."""

    def write_memory(self, addr: int, data: bytes) -> None:
        """Write *data* at *addr*.

        Writes that fall entirely outside flash go straight to :meth:`_write_raw_memory`. Writes
        into a flash region are performed as a sector-granular read-modify-write: each affected
        erase block is read, patched, and reprogrammed (the erase is part of reprogramming), so a
        single changed byte preserves the rest of its sector.
        """
        data = bytes(data)
        region = self._flash_region_for(addr, len(data))
        if region is None:
            self._write_raw_memory(addr, data)
        else:
            self._rmw_flash(region, addr, data)

    @abstractmethod
    def _write_raw_memory(self, addr: int, data: bytes) -> None:
        """Write *data* at *addr* directly (RAM-style); not valid for flash regions."""

    def _program_flash_block(self, addr: int, data: bytes) -> None:
        """Erase and reprogram one full flash erase-block at *addr* with *data*.

        ``data`` is exactly one sector's worth of (already read-modified) bytes. Default: the
        backend cannot program flash.
        """
        raise NotSupportedError("this backend cannot program flash")

    def _flash_region_for(self, addr: int, length: int) -> MemoryRegion | None:
        end = addr + length
        for region in self.memory_map():
            if region.kind is RegionKind.FLASH and region.start <= addr and end <= region.end:
                return region
        return None

    def _ensure_halted(self) -> None:  # noqa: B027 - optional hook; most backends need no halting
        """Halt the core before erasing/programming flash. Default: nothing to do."""

    def _rmw_flash(self, region: MemoryRegion, addr: int, data: bytes) -> None:
        blocksize = region.blocksize
        if blocksize <= 0:
            raise NotSupportedError(
                "this flash region reports no sector size, so it cannot be edited in place"
            )
        # Never erase/program flash while the core is running.
        self._ensure_halted()
        end = addr + len(data)
        base = region.start + ((addr - region.start) // blocksize) * blocksize
        while base < end:
            size = min(blocksize, region.end - base)
            current = self.read_memory(base, size)
            if len(current) != size:
                raise TargetError(
                    f"could not read flash sector at 0x{base:08x} for read-modify-write"
                )
            sector = bytearray(current)
            lo, hi = max(addr, base), min(end, base + size)
            sector[lo - base : hi - base] = data[lo - addr : hi - addr]
            self._program_flash_block(base, bytes(sector))
            base += size

    @abstractmethod
    def program(
        self,
        image: FirmwareImage,
        progress: ProgressCallback | None = None,
        *,
        verify: bool = True,
        run_after: bool = False,
    ) -> ProgramResult:
        """Flash *image* into the target, optionally verifying and running afterwards."""

    @abstractmethod
    def erase(self, spec: EraseSpec) -> None:
        """Erase flash according to *spec* (mass or range)."""

    @abstractmethod
    def reset(self, *, halt: bool = False) -> None:
        """Reset the target, optionally halting immediately."""

    @abstractmethod
    def raw_monitor(self, command: str) -> str:
        """Run a raw backend ``monitor`` command and return its text output (escape hatch)."""

    def is_attached(self) -> bool:
        """Whether a target is currently attached. Subclasses that can detach override this."""
        return False

    @abstractmethod
    def detach(self) -> None:
        """Detach from the current target but keep the probe open."""

    @abstractmethod
    def close(self) -> None:
        """Close the probe and release all resources."""

    # Opt-in register access (default: unsupported).

    def read_registers(self) -> dict[str, int]:
        raise NotSupportedError("this backend does not support register access yet")

    def write_register(self, name: str, value: int) -> None:
        raise NotSupportedError("this backend does not support register access yet")
