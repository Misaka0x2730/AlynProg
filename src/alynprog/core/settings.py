"""Typed wrapper over :class:`QSettings` for persisted user preferences and window state.

Storage uses the platform-native backend (registry on Windows, plist on macOS, ini on Linux) via
``QSettings(APP_ORG, APP_NAME)``. Tests redirect this to a temporary ini file with
:func:`QSettings.setPath`.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QByteArray, QSettings

from alynprog import APP_NAME, APP_ORG

_MAX_RECENT = 10
_MAX_RECENT_GOTO = 5

# QSettings keys are path-like; keep a probe serial safe to embed as one path segment.
_SERIAL_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def _sanitize_serial(serial: str) -> str:
    return _SERIAL_UNSAFE.sub("_", serial) or "_"


def _to_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _to_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if value is None:
        return default
    return bool(value)


class Settings:
    """Convenience accessors over a single :class:`QSettings` instance."""

    def __init__(self, qsettings: QSettings | None = None) -> None:
        self._s = qsettings or QSettings(APP_ORG, APP_NAME)

    @property
    def raw(self) -> QSettings:
        return self._s

    def sync(self) -> None:
        self._s.sync()

    # --- General ---------------------------------------------------------------

    @property
    def theme_mode(self) -> str | None:
        value = self._s.value("ui/theme_mode")
        return str(value) if value is not None else None

    @theme_mode.setter
    def theme_mode(self, value: str) -> None:
        self._s.setValue("ui/theme_mode", value)

    @property
    def gdb_path(self) -> str:
        return str(self._s.value("tools/gdb_path", "") or "")

    @gdb_path.setter
    def gdb_path(self, value: str) -> None:
        self._s.setValue("tools/gdb_path", value)

    # --- Connection ------------------------------------------------------------

    @property
    def last_probe_serial(self) -> str:
        return str(self._s.value("connect/last_probe_serial", "") or "")

    @last_probe_serial.setter
    def last_probe_serial(self, value: str) -> None:
        self._s.setValue("connect/last_probe_serial", value)

    @property
    def port_override(self) -> str:
        return str(self._s.value("connect/port_override", "") or "")

    @port_override.setter
    def port_override(self, value: str) -> None:
        self._s.setValue("connect/port_override", value)

    @property
    def target_power(self) -> bool:
        return _to_bool(self._s.value("connect/target_power"), False)

    @target_power.setter
    def target_power(self, value: bool) -> None:
        self._s.setValue("connect/target_power", bool(value))

    @property
    def connect_under_reset(self) -> bool:
        return _to_bool(self._s.value("connect/connect_under_reset"), False)

    @connect_under_reset.setter
    def connect_under_reset(self, value: bool) -> None:
        self._s.setValue("connect/connect_under_reset", bool(value))

    @property
    def tpwr_delay_ms(self) -> int:
        return _to_int(self._s.value("connect/tpwr_delay_ms"), 500)

    @tpwr_delay_ms.setter
    def tpwr_delay_ms(self, value: int) -> None:
        self._s.setValue("connect/tpwr_delay_ms", int(value))

    @property
    def interface_speed_hz(self) -> int:
        return _to_int(self._s.value("connect/interface_speed_hz"), 1_000_000)

    @interface_speed_hz.setter
    def interface_speed_hz(self, value: int) -> None:
        self._s.setValue("connect/interface_speed_hz", int(value))

    # --- pyOCD target selection (per probe, with a global fallback) -------------

    def pyocd_last_target(self, serial: str) -> str:
        """The last target chosen for this probe, falling back to the most recent global one."""
        if serial:
            value = self._s.value(f"pyocd/last_target/{_sanitize_serial(serial)}")
            if value:
                return str(value)
        return str(self._s.value("pyocd/last_target", "") or "")

    def set_pyocd_last_target(self, serial: str, name: str) -> None:
        if serial:
            self._s.setValue(f"pyocd/last_target/{_sanitize_serial(serial)}", name)
        self._s.setValue("pyocd/last_target", name)

    # --- Memory / hex view -----------------------------------------------------

    @property
    def hex_width(self) -> int:
        return _to_int(self._s.value("memory/hex_width"), 1)

    @hex_width.setter
    def hex_width(self, value: int) -> None:
        self._s.setValue("memory/hex_width", int(value))

    @property
    def recent_goto_addresses(self) -> list[str]:
        value = self._s.value("memory/recent_goto", [])
        if isinstance(value, str):
            return [value] if value else []
        if value is None:
            return []
        return [str(v) for v in value]

    def push_recent_goto(self, address: str) -> None:
        items = [a for a in self.recent_goto_addresses if a != address]
        items.insert(0, address)
        del items[_MAX_RECENT_GOTO:]
        self._s.setValue("memory/recent_goto", items)

    # --- Programming -----------------------------------------------------------

    @property
    def last_bin_base_addr(self) -> int:
        return _to_int(self._s.value("program/last_bin_base_addr"), 0x0800_0000)

    @last_bin_base_addr.setter
    def last_bin_base_addr(self, value: int) -> None:
        self._s.setValue("program/last_bin_base_addr", int(value))

    @property
    def recent_images(self) -> list[str]:
        value = self._s.value("program/recent_images", [])
        if isinstance(value, str):
            return [value] if value else []
        if value is None:
            return []
        return [str(v) for v in value]

    def push_recent_image(self, path: str) -> None:
        items = [p for p in self.recent_images if p != path]
        items.insert(0, path)
        del items[_MAX_RECENT:]
        self._s.setValue("program/recent_images", items)

    # --- Window state ----------------------------------------------------------

    @property
    def window_geometry(self) -> QByteArray:
        value = self._s.value("window/geometry")
        return value if isinstance(value, QByteArray) else QByteArray()

    @window_geometry.setter
    def window_geometry(self, value: QByteArray) -> None:
        self._s.setValue("window/geometry", value)

    @property
    def window_state(self) -> QByteArray:
        value = self._s.value("window/state")
        return value if isinstance(value, QByteArray) else QByteArray()

    @window_state.setter
    def window_state(self, value: QByteArray) -> None:
        self._s.setValue("window/state", value)
