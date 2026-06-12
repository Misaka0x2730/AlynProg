"""A fully in-process simulated-target backend.

It implements :class:`ProbeBackend` directly over simple byte stores — no GDB, no subprocess — which
serves two purposes: it drives the whole UI without hardware (``alynprog --fake``), and it proves
the backend interface is not GDB-shaped (the future native pyOCD backend will look like this one,
not like the BMP backend). Optional ``op_delay`` lets UI tests simulate long operations to verify
the event loop stays responsive.
"""

from __future__ import annotations

import time

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
from alynprog.core.errors import TargetError
from alynprog.core.image import FirmwareImage

FLASH_BASE = 0x0800_0000
FLASH_SIZE = 0x0010_0000  # 1 MiB
FLASH_BLOCKSIZE = 0x0000_4000  # 16 KiB sectors -> 64 sectors
RAM_BASE = 0x2000_0000
RAM_SIZE = 0x0002_0000  # 128 KiB

_REGISTERS = ("r0", "r1", "r2", "r3", "sp", "lr", "pc", "xpsr")


class FakeBackend(ProbeBackend):
    name = "fake"

    def __init__(self, *, op_delay: float = 0.0) -> None:
        self._op_delay = op_delay
        self._flash = bytearray(b"\xff" * FLASH_SIZE)
        self._ram = bytearray(RAM_SIZE)  # zero-filled
        self._scratch: dict[int, int] = {}  # writes outside known regions
        self._attached = False
        self._connected = False

    @classmethod
    def discover(cls) -> list[ProbeInfo]:
        return [
            ProbeInfo(
                backend="fake",
                identifier="fake-0",
                display_name="Simulated Target (fake)",
                serial="FAKE0001",
            )
        ]

    def connect(self, probe: ProbeInfo, options: ConnectOptions) -> None:
        self._connected = True

    def capabilities(self) -> ProbeCapabilities:
        return ProbeCapabilities(
            architectures=frozenset({"cortex-m"}),
            supports_mass_erase=True,
            supports_sector_erase=True,
            supports_target_power=True,
            supports_connect_under_reset=True,
            supports_run_control=True,
            supports_register_access=True,
            needs_external_binary=False,
            verify_method=VerifyMethod.READBACK,
        )

    def scan_targets(self) -> list[TargetInfo]:
        self._sleep()
        return [TargetInfo(index=1, name="Simulated Cortex-M4")]

    def attach(self, index: int) -> None:
        self._attached = True

    def is_attached(self) -> bool:
        return self._attached

    def memory_map(self) -> list[MemoryRegion]:
        return [
            MemoryRegion(
                FLASH_BASE, FLASH_SIZE, RegionKind.FLASH, name="Flash", blocksize=FLASH_BLOCKSIZE
            ),
            MemoryRegion(RAM_BASE, RAM_SIZE, RegionKind.RAM, name="SRAM"),
        ]

    # --- memory ----------------------------------------------------------------

    def read_memory(self, addr: int, size: int) -> bytes:
        return bytes(self._read_byte(addr + i) for i in range(size))

    def _read_byte(self, addr: int) -> int:
        if FLASH_BASE <= addr < FLASH_BASE + FLASH_SIZE:
            return self._flash[addr - FLASH_BASE]
        if RAM_BASE <= addr < RAM_BASE + RAM_SIZE:
            return self._ram[addr - RAM_BASE]
        return self._scratch.get(addr, 0x00)

    def _write_raw_memory(self, addr: int, data: bytes) -> None:
        # Direct write for RAM / scratch. Flash is handled by the read-modify-write path in
        # ProbeBackend.write_memory, which calls _program_flash_block below.
        for i, value in enumerate(data):
            target = addr + i
            if FLASH_BASE <= target < FLASH_BASE + FLASH_SIZE:
                raise TargetError(
                    f"cannot write to flash at 0x{target:08x}; erase and program it instead"
                )
            if RAM_BASE <= target < RAM_BASE + RAM_SIZE:
                self._ram[target - RAM_BASE] = value
            else:
                self._scratch[target] = value

    def _program_flash_block(self, addr: int, data: bytes) -> None:
        # Simulate erasing + programming a full sector: store the (already read-modified) bytes.
        for i, value in enumerate(data):
            target = addr + i
            if FLASH_BASE <= target < FLASH_BASE + FLASH_SIZE:
                self._flash[target - FLASH_BASE] = value

    # --- programming -----------------------------------------------------------

    def program(
        self,
        image: FirmwareImage,
        progress: ProgressCallback | None = None,
        *,
        verify: bool = True,
        run_after: bool = False,
    ) -> ProgramResult:
        total = image.total_size
        # ELF segments are not parsed; simulate a fixed-size payload for progress.
        if total == 0:
            total = 64 * 1024
        steps = 10
        for step in range(1, steps + 1):
            self._sleep()
            if progress:
                progress(total * step // steps, total)

        for segment in image.segments:
            self._program_flash_block(segment.addr, segment.data)

        verified: bool | None = None
        sections: tuple[CompareResult, ...] = ()
        if verify:
            ok = all(
                self.read_memory(seg.addr, len(seg.data)) == seg.data for seg in image.segments
            )
            verified = ok
            sections = (CompareResult(name="image", matched=ok),)
        if run_after:
            # Mirror a real probe: resetting and running detaches the debug session.
            self._attached = False
        return ProgramResult(bytes_written=total, verified=verified, sections=sections)

    # --- erase / reset ---------------------------------------------------------

    def erase(self, spec: EraseSpec) -> None:
        self._sleep()
        if spec.kind is EraseKind.MASS:
            self._flash[:] = b"\xff" * FLASH_SIZE
            return
        start = max(spec.addr, FLASH_BASE) - FLASH_BASE
        end = min(spec.addr + spec.length, FLASH_BASE + FLASH_SIZE) - FLASH_BASE
        if start < end:
            self._flash[start:end] = b"\xff" * (end - start)

    def reset(self, *, halt: bool = False) -> None:
        self._sleep()

    def raw_monitor(self, command: str) -> str:
        return f"(fake) monitor {command}: ok"

    def read_registers(self) -> dict[str, int]:
        return {name: i * 0x11111111 & 0xFFFFFFFF for i, name in enumerate(_REGISTERS)}

    def write_register(self, name: str, value: int) -> None:
        if name not in _REGISTERS:
            raise TargetError(f"unknown register {name!r}")

    def detach(self) -> None:
        self._attached = False

    def close(self) -> None:
        self._attached = False
        self._connected = False

    def _sleep(self) -> None:
        if self._op_delay > 0:
            time.sleep(self._op_delay)
