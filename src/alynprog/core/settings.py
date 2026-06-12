"""Typed wrapper over :class:`QSettings` for persisted user preferences and window state.

Storage uses the platform-native backend (registry on Windows, plist on macOS, ini on Linux) via
``QSettings(APP_ORG, APP_NAME)``. Tests redirect this to a temporary ini file with
:func:`QSettings.setPath`.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings

from alynprog import APP_NAME, APP_ORG

_MAX_RECENT = 10


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

    # --- Memory / hex view -----------------------------------------------------

    @property
    def hex_width(self) -> int:
        return _to_int(self._s.value("memory/hex_width"), 1)

    @hex_width.setter
    def hex_width(self, value: int) -> None:
        self._s.setValue("memory/hex_width", int(value))

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
