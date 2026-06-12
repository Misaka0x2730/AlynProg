"""Tests for the QSettings-backed Settings wrapper (isolated to a temp ini file)."""

from __future__ import annotations


def test_round_trips(temp_settings):
    s = temp_settings
    s.gdb_path = "/usr/bin/arm-none-eabi-gdb"
    s.theme_mode = "dark"
    s.last_probe_serial = "ABC123"
    s.hex_width = 4
    s.target_power = True
    s.tpwr_delay_ms = 750
    s.interface_speed_hz = 2_000_000
    s.last_bin_base_addr = 0x0800_4000
    s.sync()

    from alynprog.core.settings import Settings

    reloaded = Settings(s.raw)
    assert reloaded.gdb_path == "/usr/bin/arm-none-eabi-gdb"
    assert reloaded.theme_mode == "dark"
    assert reloaded.last_probe_serial == "ABC123"
    assert reloaded.hex_width == 4
    assert reloaded.target_power is True
    assert reloaded.tpwr_delay_ms == 750
    assert reloaded.interface_speed_hz == 2_000_000
    assert reloaded.last_bin_base_addr == 0x0800_4000


def test_defaults(temp_settings):
    assert temp_settings.gdb_path == ""
    assert temp_settings.theme_mode is None
    assert temp_settings.hex_width == 1
    assert temp_settings.target_power is False
    assert temp_settings.tpwr_delay_ms == 500
    assert temp_settings.interface_speed_hz == 1_000_000
    assert temp_settings.recent_images == []


def test_recent_images_dedup_and_cap(temp_settings):
    for i in range(15):
        temp_settings.push_recent_image(f"/fw/{i}.elf")
    temp_settings.push_recent_image("/fw/0.elf")  # move to front, no dup
    recent = temp_settings.recent_images
    assert len(recent) == 10
    assert recent[0] == "/fw/0.elf"
    assert len(set(recent)) == len(recent)


def test_recent_goto_dedup_and_cap(temp_settings):
    assert temp_settings.recent_goto_addresses == []
    for i in range(8):
        temp_settings.push_recent_goto(f"0x{0x0800_0000 + i * 0x100:08X}")
    temp_settings.push_recent_goto("0x08000000")  # move to front, no dup
    recent = temp_settings.recent_goto_addresses
    assert len(recent) == 5  # capped at 5
    assert recent[0] == "0x08000000"
    assert len(set(recent)) == len(recent)
