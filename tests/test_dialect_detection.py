"""Unit tests for BMP monitor-dialect detection from firmware output."""

from __future__ import annotations

from alynprog.backends.bmp.dialect import BmpDialect

_HELP_V1 = (
    "swdp_scan -- scan\njtag_scan -- scan\nreset -- reset\n"
    "erase_mass -- erase\ntpwr -- power\nconnect_rst -- reset"
)
_HELP_V2 = (
    "swd_scan -- scan\nswdp_scan -- alias\njtag_scan -- scan\nreset -- reset\n"
    "erase_mass -- erase\nerase_range -- erase\ntpwr -- power\nconnect_rst -- reset"
)
_HELP_NO_ERASE = "swdp_scan -- scan\njtag_scan -- scan\nreset -- reset\ntpwr -- power"


def test_v1_prefers_swdp_scan_and_has_mass_erase_only():
    d = BmpDialect.detect("Black Magic Probe (Firmware v1.10.2)", _HELP_V1)
    assert d.scan_command("swd") == "swdp_scan"
    assert d.scan_command("jtag") == "jtag_scan"
    assert d.mass_erase_command() == "erase_mass"
    assert d.range_erase_command(0x0800_0000, 0x1000) is None
    feats = d.features()
    assert feats.has_mass_erase and not feats.has_range_erase
    assert feats.has_target_power and feats.has_connect_under_reset


def test_v2_has_range_erase_and_still_prefers_swdp_scan():
    d = BmpDialect.detect("Black Magic Probe (Firmware v2.0.0)", _HELP_V2)
    # swdp_scan is preferred for compatibility even though swd_scan exists.
    assert d.scan_command("swd") == "swdp_scan"
    assert d.range_erase_command(0x0800_0000, 0x1000) == "erase_range 0x08000000 0x1000"
    assert d.features().has_range_erase


def test_no_erase_profile_reports_no_erase_capability():
    d = BmpDialect.detect("Black Magic Probe", _HELP_NO_ERASE)
    assert d.mass_erase_command() is None
    assert d.range_erase_command(0, 16) is None
    assert not d.features().has_mass_erase


def test_target_power_and_connect_reset_commands():
    d = BmpDialect.detect("", _HELP_V2)
    assert d.target_power_command(True) == "tpwr enable"
    assert d.connect_under_reset_command(False) == "connect_rst disable"


def test_frequency_command():
    with_freq = BmpDialect.detect("", _HELP_V2 + "\nfrequency -- set the SWD/JTAG clock")
    assert with_freq.frequency_command(1_000_000) == "frequency 1000000"
    assert with_freq.frequency_command(0) is None  # invalid speed

    without = BmpDialect.detect("", _HELP_V2)  # no 'frequency' in help
    assert without.frequency_command(1_000_000) is None
