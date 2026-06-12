"""In-process fakes for the pyOCD library, mirroring the slice that PyocdBackend uses.

Wired into a real :class:`PyocdApi` via :func:`make_fake_api`, these let the backend's full
lifecycle run without hardware or a live pyOCD session. Real pyOCD enums and the real exceptions
module are used so the fakes stay faithful (the backend classifies errors against the real
hierarchy, and converts memory regions via the real ``MemoryType``/``Target.State`` names).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pyocd.core import exceptions
from pyocd.core.memory_map import MemoryType
from pyocd.core.target import Target
from pyocd.flash.eraser import FlashEraser

from alynprog.backends.pyocd._api import PyocdApi

FLASH_BASE = 0x0800_0000
FLASH_SIZE = 0x0008_0000  # 512 KiB
FLASH_SECTOR = 0x0000_0800  # 2 KiB sectors
RAM_BASE = 0x2000_0000
RAM_SIZE = 0x0001_0000  # 64 KiB


class FakeMemory:
    """Flash + RAM byte stores addressed by absolute address."""

    def __init__(self) -> None:
        self.flash = bytearray(b"\xff" * FLASH_SIZE)
        self.ram = bytearray(RAM_SIZE)

    def read_byte(self, addr: int) -> int:
        if FLASH_BASE <= addr < FLASH_BASE + FLASH_SIZE:
            return self.flash[addr - FLASH_BASE]
        if RAM_BASE <= addr < RAM_BASE + RAM_SIZE:
            return self.ram[addr - RAM_BASE]
        return 0x00

    def write_byte(self, addr: int, value: int) -> None:
        if FLASH_BASE <= addr < FLASH_BASE + FLASH_SIZE:
            self.flash[addr - FLASH_BASE] = value & 0xFF
        elif RAM_BASE <= addr < RAM_BASE + RAM_SIZE:
            self.ram[addr - RAM_BASE] = value & 0xFF

    def write_block(self, addr: int, data: bytes) -> None:
        for i, byte in enumerate(data):
            self.write_byte(addr + i, byte)


@dataclass
class FakeRig:
    """Shared state across a fake session lifecycle, plus error-injection knobs."""

    memory: FakeMemory = field(default_factory=FakeMemory)
    program_error: BaseException | None = None
    read_error: BaseException | None = None
    erase_error: BaseException | None = None


@dataclass
class FakeDebugProbe:
    unique_id: str = "FAKEUID0001"
    vendor_name: str = "FakeSemi"
    product_name: str = "Fake CMSIS-DAP"
    description: str = "Fake CMSIS-DAP"


class FakeMemRegion:
    def __init__(self, mem_type, start: int, length: int, blocksize: int | None) -> None:
        self.type = mem_type
        self.start = start
        self.length = length
        self.blocksize = blocksize
        self.sector_size = blocksize
        self.name = mem_type.name.lower()


class FakeSoCTarget:
    def __init__(self, rig: FakeRig) -> None:
        self._rig = rig
        self._state = Target.State.RUNNING

    def get_memory_map(self):
        return [
            FakeMemRegion(MemoryType.FLASH, FLASH_BASE, FLASH_SIZE, FLASH_SECTOR),
            FakeMemRegion(MemoryType.RAM, RAM_BASE, RAM_SIZE, None),
        ]

    def get_state(self):
        return self._state

    def halt(self) -> None:
        self._state = Target.State.HALTED

    def resume(self) -> None:
        self._state = Target.State.RUNNING

    def reset(self, *_args, **_kwargs) -> None:
        self._state = Target.State.RUNNING

    def reset_and_halt(self, *_args, **_kwargs) -> None:
        self._state = Target.State.HALTED

    def read_memory_block8(self, addr: int, size: int):
        if self._rig.read_error is not None:
            raise self._rig.read_error
        return [self._rig.memory.read_byte(addr + i) for i in range(size)]

    def write_memory_block8(self, addr: int, data) -> None:
        for i, value in enumerate(data):
            self._rig.memory.write_byte(addr + i, value)


class FakeSession:
    def __init__(self, probe, rig: FakeRig, *, auto_open: bool = True, options=None) -> None:
        self.probe = probe
        self._rig = rig
        self.options = options or {}
        self.target = None
        self.is_open = False
        if auto_open:
            self.open()

    def open(self, init_board: bool = True) -> FakeSession:
        self.target = FakeSoCTarget(self._rig)
        self.is_open = True
        return self

    def close(self) -> None:
        self.is_open = False


class FakeFileProgrammer:
    def __init__(self, session: FakeSession, progress=None, **_kwargs) -> None:
        self._rig = session._rig
        self._progress = progress

    def program(self, file_or_path, file_format=None, base_address=None, **_kwargs) -> None:
        if self._rig.program_error is not None:
            raise self._rig.program_error
        self._apply(file_or_path, file_format or "bin", base_address)
        if self._progress is not None:
            for step in range(1, 11):
                self._progress(step / 10.0)

    def _apply(self, file_or_path, fmt: str, base_address) -> None:
        if fmt == "bin":
            self._rig.memory.write_block(base_address or 0, self._read_bytes(file_or_path))
        elif fmt == "hex":
            from intelhex import IntelHex

            ih = IntelHex(str(file_or_path))
            for addr in ih.addresses():
                self._rig.memory.write_byte(addr, ih[addr])
        # elf/axf: the fake does not parse them; programming "succeeds" without writing.

    @staticmethod
    def _read_bytes(file_or_path) -> bytes:
        if hasattr(file_or_path, "read"):
            return file_or_path.read()
        with open(file_or_path, "rb") as handle:
            return handle.read()


class FakeFlashEraser:
    def __init__(self, session: FakeSession, mode) -> None:
        self._rig = session._rig
        self._mode = mode

    def erase(self, addresses=None) -> None:
        if self._rig.erase_error is not None:
            raise self._rig.erase_error
        if self._mode.name == "SECTOR":
            for spec in addresses or []:
                start, length = self._parse(spec)
                # Real pyOCD aligns down to the sector boundary and erases whole sectors.
                addr = start - ((start - FLASH_BASE) % FLASH_SECTOR)
                end = start + length
                while addr < end:
                    for i in range(FLASH_SECTOR):
                        self._rig.memory.write_byte(addr + i, 0xFF)
                    addr += FLASH_SECTOR
        else:  # CHIP / MASS: erase the whole flash
            self._rig.memory.flash[:] = b"\xff" * FLASH_SIZE

    @staticmethod
    def _parse(spec: str) -> tuple[int, int]:
        addr_str, len_str = spec.split("+")
        return int(addr_str, 16), int(len_str, 16)


def make_fake_api(rig: FakeRig | None = None, *, probes=None, target_list=None) -> PyocdApi:
    """Build a :class:`PyocdApi` backed by the fakes above (and an optional shared *rig*)."""
    rig = rig or FakeRig()
    probe_list = list(probes) if probes is not None else [FakeDebugProbe()]

    def get_all_connected_probes(blocking=True, unique_id=None, print_wait_message=True):
        if unique_id is None:
            return list(probe_list)
        return [p for p in probe_list if p.unique_id == unique_id]

    def session_factory(probe, auto_open=True, options=None):
        return FakeSession(probe, rig, auto_open=auto_open, options=options)

    def list_targets(**_kwargs):
        if target_list is not None:
            return target_list
        return {"pyocd_version": "0.44.1", "targets": []}

    return PyocdApi(
        get_all_connected_probes=get_all_connected_probes,
        session_factory=session_factory,
        file_programmer=FakeFileProgrammer,
        flash_eraser=FakeFlashEraser,
        eraser_mode=FlashEraser.Mode,
        list_targets=list_targets,
        exceptions=exceptions,
    )
