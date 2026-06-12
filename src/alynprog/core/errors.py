"""Exception hierarchy shared across transports, backends and the session layer."""

from __future__ import annotations


class AlynProgError(Exception):
    """Root of all AlynProg-specific exceptions."""


# --- Transport / GDB-MI layer -------------------------------------------------


class TransportError(AlynProgError):
    """A failure in the GDB/MI transport (subprocess, pipes, protocol)."""


class GdbError(TransportError):
    """GDB replied with an ``^error`` result record.

    ``message`` carries GDB's own human-readable error text, which higher layers may map to a
    friendlier message.
    """

    def __init__(self, message: str, *, command: str | None = None) -> None:
        self.message = message
        self.command = command
        if command:
            super().__init__(f"{message} (while running: {command})")
        else:
            super().__init__(message)


class MiTimeoutError(TransportError):
    """A GDB/MI command did not produce a result record within its timeout."""

    def __init__(self, command: str, timeout: float) -> None:
        self.command = command
        self.timeout = timeout
        super().__init__(f"timed out after {timeout:g}s waiting for: {command}")


class SessionDied(TransportError):
    """The GDB subprocess exited or its pipes closed unexpectedly."""


# --- Backend / target layer ---------------------------------------------------


class ProbeError(AlynProgError):
    """A debug-probe level failure (discovery, connection, configuration)."""


class TargetError(AlynProgError):
    """A target-level failure (scan/attach/erase/program/memory access)."""


class NotSupportedError(AlynProgError):
    """The requested operation is not supported by the active backend/target.

    Raised, for example, by ``read_registers()`` on a backend that has not implemented register
    access yet, or by ``erase_range()`` when the target lacks sector erase.
    """


# --- Firmware image layer -----------------------------------------------------


class ImageError(AlynProgError):
    """A firmware image could not be parsed or is missing required information."""
