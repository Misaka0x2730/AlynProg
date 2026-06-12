"""Abstract description of a debug server's ``monitor`` command vocabulary.

Different GDB-server backends (Black Magic Probe, OpenOCD, …) accept different ``monitor``
sub-commands for scanning, reset and erase. A :class:`MonitorDialect` lets the GDB-based backends
share one transport while differing only in this vocabulary. Concrete dialects live next to their
backend (e.g. ``alynprog.backends.bmp.dialect``); this module only defines the interface and the
data describing what a dialect supports.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

Transport = str  # "swd" | "jtag"


@dataclass(frozen=True)
class DialectFeatures:
    """What a detected dialect/firmware combination can do (drives :class:`ProbeCapabilities`)."""

    has_mass_erase: bool = False
    has_range_erase: bool = False
    has_target_power: bool = False
    has_connect_under_reset: bool = False


class MonitorDialect(ABC):
    """Maps abstract operations to a backend's concrete ``monitor`` command strings."""

    @abstractmethod
    def scan_command(self, transport: Transport) -> str:
        """The ``monitor`` command that scans the bus and lists targets."""

    @abstractmethod
    def reset_command(self) -> str:
        """The ``monitor`` command that resets the target."""

    def mass_erase_command(self) -> str | None:
        """The ``monitor`` command for a whole-chip erase, or ``None`` if unsupported."""
        return None

    def range_erase_command(self, addr: int, length: int) -> str | None:
        """The ``monitor`` command to erase ``[addr, addr+length)``, or ``None`` if unsupported."""
        return None

    def target_power_command(self, enabled: bool) -> str | None:
        """The ``monitor`` command to toggle target power, or ``None`` if unsupported."""
        return None

    def features(self) -> DialectFeatures:
        """Capabilities implied by this dialect (may be refined after attaching to a target)."""
        return DialectFeatures()
