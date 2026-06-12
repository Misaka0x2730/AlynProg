"""The Black Magic Probe ``monitor`` command dialect, detected from firmware output.

BMP firmware has drifted over versions (notably the ``swdp_scan`` → ``swd_scan`` rename with a
backwards-compatible alias, and the mass-erase overhaul in v2.0.0). Rather than hard-code commands,
we detect what the connected firmware offers from ``monitor version`` and ``monitor help`` and build
a dialect describing it. ``swdp_scan`` is preferred when available because it is the documented form
accepted across the widest firmware range.
"""

from __future__ import annotations

from alynprog.transports.dialects import DialectFeatures, MonitorDialect, Transport


class BmpDialect(MonitorDialect):
    def __init__(
        self,
        *,
        scan_cmd: str = "swdp_scan",
        jtag_scan_cmd: str = "jtag_scan",
        reset_cmd: str = "reset",
        has_mass_erase: bool = False,
        has_range_erase: bool = False,
        has_target_power: bool = False,
        has_connect_under_reset: bool = False,
        has_frequency: bool = False,
        connect_reset_cmd: str = "connect_rst",
        version_text: str = "",
    ) -> None:
        self._scan_cmd = scan_cmd
        self._jtag_scan_cmd = jtag_scan_cmd
        self._reset_cmd = reset_cmd
        self._has_mass_erase = has_mass_erase
        self._has_range_erase = has_range_erase
        self._has_target_power = has_target_power
        self._has_connect_under_reset = has_connect_under_reset
        self._has_frequency = has_frequency
        self._connect_reset_cmd = connect_reset_cmd
        self.version_text = version_text.strip()

    @classmethod
    def detect(cls, version_text: str, help_text: str) -> BmpDialect:
        help_lower = help_text.lower()
        if "swdp_scan" in help_lower:
            scan_cmd = "swdp_scan"
        elif "swd_scan" in help_lower:
            scan_cmd = "swd_scan"
        else:
            scan_cmd = "swdp_scan"  # best-effort default for terse/old help output

        if "connect_rst" in help_lower:
            connect_reset_cmd = "connect_rst"
        elif "connect_srst" in help_lower:
            connect_reset_cmd = "connect_srst"
        else:
            connect_reset_cmd = "connect_rst"

        return cls(
            scan_cmd=scan_cmd,
            reset_cmd="reset",
            has_mass_erase="erase_mass" in help_lower,
            has_range_erase="erase_range" in help_lower,
            has_target_power="tpwr" in help_lower,
            has_connect_under_reset="connect_rst" in help_lower or "connect_srst" in help_lower,
            has_frequency="frequency" in help_lower,
            connect_reset_cmd=connect_reset_cmd,
            version_text=version_text,
        )

    # --- MonitorDialect interface ---------------------------------------------

    def scan_command(self, transport: Transport) -> str:
        return self._jtag_scan_cmd if transport == "jtag" else self._scan_cmd

    def reset_command(self) -> str:
        return self._reset_cmd

    def mass_erase_command(self) -> str | None:
        return "erase_mass" if self._has_mass_erase else None

    def range_erase_command(self, addr: int, length: int) -> str | None:
        if not self._has_range_erase:
            return None
        return f"erase_range 0x{addr:08x} 0x{length:x}"

    def target_power_command(self, enabled: bool) -> str | None:
        if not self._has_target_power:
            return None
        return f"tpwr {'enable' if enabled else 'disable'}"

    def connect_under_reset_command(self, enabled: bool) -> str | None:
        if not self._has_connect_under_reset:
            return None
        return f"{self._connect_reset_cmd} {'enable' if enabled else 'disable'}"

    def frequency_command(self, hz: int) -> str | None:
        # BMP 'monitor frequency <hz>' sets the SWD/JTAG clock; it accepts a plain value in Hz.
        if not self._has_frequency or hz <= 0:
            return None
        return f"frequency {hz}"

    def features(self) -> DialectFeatures:
        return DialectFeatures(
            has_mass_erase=self._has_mass_erase,
            has_range_erase=self._has_range_erase,
            has_target_power=self._has_target_power,
            has_connect_under_reset=self._has_connect_under_reset,
        )
