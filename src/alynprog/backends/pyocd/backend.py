"""Native, in-process pyOCD backend.

Implements the byte/image-oriented :class:`ProbeBackend` contract directly over pyOCD's Python API
(``Session`` / ``SoCTarget`` / ``FileProgrammer`` / ``FlashEraser``) — no GDB, no subprocess. pyOCD
cannot auto-detect the target, so the user picks one (carried in ``ConnectOptions.target_name``);
``scan_targets`` just echoes that choice to fit the connect->scan->attach state machine.

No host-side readback verification is layered on top: pyOCD owns programming, so ``program`` reports
``verified=None`` and ``capabilities`` advertises ``VerifyMethod.NONE``.
"""

from __future__ import annotations

import contextlib
import io
import logging

from alynprog.backends.pyocd._api import PyocdApi
from alynprog.backends.pyocd.discovery import discover_pyocd_probes
from alynprog.backends.pyocd.errors import mapped_errors
from alynprog.core.backend import (
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
from alynprog.core.errors import NotSupportedError, ProbeError, TargetError
from alynprog.core.image import FirmwareImage, ImageKind

_REGION_KIND = {"FLASH": RegionKind.FLASH, "RAM": RegionKind.RAM}
_FILE_FORMAT = {ImageKind.ELF: "elf", ImageKind.HEX: "hex", ImageKind.BIN: "bin"}
# Synthetic progress denominator for ELF, whose written size is unknown (segments aren't parsed).
_ELF_PROGRESS_TOTAL = 64 * 1024


class PyocdBackend(ProbeBackend):
    name = "pyocd"

    def __init__(self, api: PyocdApi | None = None, log_callback=None) -> None:
        self._api = api
        self._log_callback = log_callback
        self._probe = None
        self._options: ConnectOptions | None = None
        self._session = None
        self._target = None
        self._attached = False
        self._log_handler: logging.Handler | None = None

    @classmethod
    def create(cls, *, gdb_path: str = "", log_callback=None) -> ProbeBackend:
        # gdb_path is irrelevant to pyOCD; the worker passes it uniformly, so we just ignore it.
        return cls(log_callback=log_callback)

    @classmethod
    def discover(cls) -> list[ProbeInfo]:
        return discover_pyocd_probes()

    # --- connection ------------------------------------------------------------

    def connect(self, probe: ProbeInfo, options: ConnectOptions) -> None:
        api = self._ensure_api()
        if not options.target_name:
            raise TargetError("no pyOCD target selected; choose a target before connecting")
        with mapped_errors("connecting"):
            matches = api.get_all_connected_probes(
                blocking=False, unique_id=probe.identifier, print_wait_message=False
            )
        if not matches:
            raise ProbeError(f"pyOCD probe {probe.identifier} is no longer connected")
        self._probe = matches[0]
        self._options = options
        self._install_log_handler()

    def capabilities(self) -> ProbeCapabilities:
        return ProbeCapabilities(
            architectures=frozenset({"cortex-m"}),
            supports_mass_erase=True,
            supports_sector_erase=True,
            supports_target_power=False,
            supports_connect_under_reset=True,
            supports_run_control=True,
            supports_register_access=False,  # deferred; the API exists but the UI is unshipped
            needs_external_binary=False,
            verify_method=VerifyMethod.NONE,
        )

    def scan_targets(self) -> list[TargetInfo]:
        name = self._options.target_name if self._options else ""
        return [TargetInfo(index=1, name=name)]

    def attach(self, index: int) -> None:
        api = self._ensure_api()
        self.detach()  # release any prior session first (re-attach is allowed)
        with mapped_errors("attaching"):
            session = api.session_factory(
                self._probe, auto_open=False, options=self._session_options()
            )
            try:
                session.open(init_board=True)
            except Exception:
                # Don't leak the probe handle if open() fails part-way (e.g. wrong target).
                with contextlib.suppress(Exception):
                    session.close()
                raise
            self._session = session
            self._target = session.target
        self._attached = True

    def is_attached(self) -> bool:
        return self._attached

    def _session_options(self) -> dict:
        options = self._options or ConnectOptions()
        return {
            "target_override": options.target_name,
            "frequency": options.interface_speed_hz,
            "connect_mode": "under-reset" if options.connect_under_reset else "halt",
            "dap_protocol": options.transport,
            "resume_on_disconnect": True,
            "hide_programming_progress": True,
        }

    # --- memory ----------------------------------------------------------------

    def memory_map(self) -> list[MemoryRegion]:
        target = self._require_target()
        with mapped_errors("reading memory map"):
            regions = target.get_memory_map()
        out: list[MemoryRegion] = []
        for region in regions:
            kind = _REGION_KIND.get(region.type.name, RegionKind.UNKNOWN)
            blocksize = (getattr(region, "blocksize", 0) or 0) if kind is RegionKind.FLASH else 0
            name = getattr(region, "name", "") or region.type.name.capitalize()
            out.append(
                MemoryRegion(
                    start=region.start,
                    size=region.length,
                    kind=kind,
                    name=name,
                    blocksize=blocksize,
                )
            )
        return out

    def read_memory(self, addr: int, size: int) -> bytes:
        target = self._require_target()
        with mapped_errors("reading memory"):
            return bytes(target.read_memory_block8(addr, size))

    def _write_raw_memory(self, addr: int, data: bytes) -> None:
        target = self._require_target()
        with mapped_errors("writing memory"):
            target.write_memory_block8(addr, list(data))

    def _program_flash_block(self, addr: int, data: bytes) -> None:
        api = self._ensure_api()
        with mapped_errors("programming flash"):
            # smart_flash=False: erase+write exactly this sector. The CRC "smart" analyzer is
            # unreliable on some clone STM32 parts, skipping erases of pages it then fails to write.
            programmer = api.file_programmer(self._session, smart_flash=False)
            programmer.program(io.BytesIO(bytes(data)), file_format="bin", base_address=addr)

    def _ensure_halted(self) -> None:
        target = self._require_target()
        with mapped_errors("halting"):
            if target.get_state().name != "HALTED":
                target.halt()

    def _reset_and_halt(self) -> None:
        # Reset+halt before erase/program so a running firmware can't interfere with the flash
        # algorithm — some clone STM32 parts HardFault (IPSR=3) when flashed from a running state.
        target = self._require_target()
        with mapped_errors("halting"):
            target.reset_and_halt()

    # --- programming / erase / reset -------------------------------------------

    def program(
        self,
        image: FirmwareImage,
        progress: ProgressCallback | None = None,
        *,
        verify: bool = True,
        run_after: bool = False,
    ) -> ProgramResult:
        api = self._ensure_api()
        self._reset_and_halt()
        total = image.total_size or _ELF_PROGRESS_TOTAL

        def adapter(fraction: float) -> None:
            if progress is not None:
                progress(min(total, max(0, int(fraction * total))), total)

        kwargs = {"base_address": image.base_addr} if image.kind is ImageKind.BIN else {}
        with mapped_errors("programming"):
            # smart_flash=False — see _program_flash_block: avoids the CRC analyzer that
            # mis-skips erases on clone parts, at the cost of always erasing the image's sectors.
            programmer = api.file_programmer(self._session, progress=adapter, smart_flash=False)
            programmer.program(str(image.path), file_format=_FILE_FORMAT[image.kind], **kwargs)
        if progress is not None:
            progress(total, total)  # guarantee a final 100%

        if run_after:
            with mapped_errors("starting target"):
                self._require_target().reset()
            self.detach()  # mimic BMP: programming-and-running detaches the debug session
        # No host-side verify: pyOCD owns programming. verified is None (not performed).
        return ProgramResult(bytes_written=total, verified=None, sections=())

    def erase(self, spec: EraseSpec) -> None:
        api = self._ensure_api()
        self._reset_and_halt()
        with mapped_errors("erasing"):
            if spec.kind is EraseKind.MASS:
                api.flash_eraser(self._session, api.eraser_mode.CHIP).erase()
            else:
                addr_spec = f"0x{spec.addr:x}+0x{spec.length:x}"
                api.flash_eraser(self._session, api.eraser_mode.SECTOR).erase([addr_spec])

    def reset(self, *, halt: bool = False) -> None:
        target = self._require_target()
        with mapped_errors("resetting"):
            if halt:
                target.reset_and_halt()
            else:
                target.reset()

    def raw_monitor(self, command: str) -> str:
        raise NotSupportedError("the pyOCD backend has no monitor console")

    # --- teardown --------------------------------------------------------------

    def detach(self) -> None:
        if self._session is not None:
            with contextlib.suppress(Exception):
                self._session.close()  # resume_on_disconnect runs the target
        self._session = None
        self._target = None
        self._attached = False

    def close(self) -> None:
        self.detach()
        self._remove_log_handler()
        self._probe = None

    # --- helpers ---------------------------------------------------------------

    def _ensure_api(self) -> PyocdApi:
        if self._api is None:
            self._api = PyocdApi.real()
        return self._api

    def _require_target(self):
        # "target is not attached" matches a friendly_error needle, so the UI shows
        # "Attach to a target first." instead of a raw AttributeError.
        if self._target is None:
            raise TargetError("target is not attached")
        return self._target

    def _install_log_handler(self) -> None:
        if self._log_callback is None or self._log_handler is not None:
            return
        handler = _LogForwarder(self._log_callback)
        handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        logging.getLogger("pyocd").addHandler(handler)
        self._log_handler = handler

    def _remove_log_handler(self) -> None:
        if self._log_handler is not None:
            logging.getLogger("pyocd").removeHandler(self._log_handler)
            self._log_handler = None


class _LogForwarder(logging.Handler):
    """Forwards pyOCD's own log records into the app's log panel via the backend log callback."""

    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        with contextlib.suppress(Exception):
            self._callback("pyocd", self.format(record))
