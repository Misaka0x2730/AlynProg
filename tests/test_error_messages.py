"""Tests for the friendly error-message mapper."""

from __future__ import annotations

from alynprog.core.error_messages import friendly_error


def test_maps_real_flash_write_error():
    # Exact text captured from a real BMP session (see docs/hardware-spike.md).
    raw = "Writing to flash memory forbidden in this context"
    assert "Programming tab" in friendly_error(raw)


def test_permission_denied_hint():
    assert "udev" in friendly_error("Permission denied").lower() or "port access" in friendly_error(
        "Permission denied"
    )


def test_unknown_passes_through():
    assert friendly_error("some novel error") == "some novel error"
