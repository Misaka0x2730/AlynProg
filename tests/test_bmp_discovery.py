"""Unit tests for BMP probe discovery and per-OS GDB-port selection (mocked pyserial)."""

from __future__ import annotations

from dataclasses import dataclass

from alynprog.backends.bmp.discovery import BMP_PID, BMP_VID, discover_bmp_probes


@dataclass
class FakePort:
    device: str
    vid: int | None = BMP_VID
    pid: int | None = BMP_PID
    serial_number: str | None = "ABCDEF123456"
    location: str | None = None
    hwid: str = ""
    name: str | None = None


def test_ignores_non_bmp_devices():
    ports = [FakePort(device="/dev/ttyUSB0", vid=0x0403, pid=0x6001)]
    assert discover_bmp_probes(ports) == []


def test_windows_picks_mi_00_port():
    ports = [
        FakePort(device="COM5", hwid="USB VID:PID=1D50:6018 MI_02"),
        FakePort(device="COM4", hwid="USB VID:PID=1D50:6018 MI_00"),
    ]
    probes = discover_bmp_probes(ports)
    assert len(probes) == 1
    assert probes[0].identifier == "COM4"
    assert probes[0].port_ambiguous is False


def test_linux_picks_interface_zero_by_location():
    ports = [
        FakePort(device="/dev/ttyACM1", location="1-1.4:1.2"),
        FakePort(device="/dev/ttyACM0", location="1-1.4:1.0"),
    ]
    probes = discover_bmp_probes(ports)
    assert probes[0].identifier == "/dev/ttyACM0"
    assert probes[0].port_ambiguous is False


def test_macos_falls_back_to_sorted_name_and_flags_ambiguous():
    ports = [
        FakePort(device="/dev/cu.usbmodemDEAD3"),
        FakePort(device="/dev/cu.usbmodemDEAD1"),
    ]
    probes = discover_bmp_probes(ports)
    assert probes[0].identifier == "/dev/cu.usbmodemDEAD1"
    assert probes[0].port_ambiguous is True


def test_single_port_is_not_ambiguous():
    ports = [FakePort(device="/dev/ttyACM0")]
    probes = discover_bmp_probes(ports)
    assert probes[0].identifier == "/dev/ttyACM0"
    assert probes[0].port_ambiguous is False
    assert probes[0].serial == "ABCDEF123456"


def test_two_distinct_probes_are_grouped_by_serial():
    ports = [
        FakePort(device="/dev/ttyACM0", serial_number="AAA", location="1-1:1.0"),
        FakePort(device="/dev/ttyACM1", serial_number="AAA", location="1-1:1.2"),
        FakePort(device="/dev/ttyACM2", serial_number="BBB", location="1-2:1.0"),
        FakePort(device="/dev/ttyACM3", serial_number="BBB", location="1-2:1.2"),
    ]
    probes = discover_bmp_probes(ports)
    assert {p.serial for p in probes} == {"AAA", "BBB"}
    assert {p.identifier for p in probes} == {"/dev/ttyACM0", "/dev/ttyACM2"}
