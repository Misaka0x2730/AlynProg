"""Map raw GDB/probe error text to friendlier, actionable messages.

The substrings below were captured from real GDB + Black Magic Probe sessions (see
``docs/hardware-spike.md``). Unknown errors pass through unchanged so no information is lost.
"""

from __future__ import annotations

# (lowercased substring, friendly message)
_KNOWN: tuple[tuple[str, str], ...] = (
    (
        "writing to flash memory forbidden",
        "Cannot write to flash from the memory view — erase and program it from the "
        "Programming tab instead.",
    ),
    ("cannot access memory", "That address is not accessible on the current target."),
    ("memory access error", "That address is not accessible on the current target."),
    ("remote communication error", "Lost communication with the probe."),
    ("remote connection closed", "The probe closed the connection."),
    (
        "permission denied",
        "Permission denied opening the probe device (check port access / udev rules).",
    ),
    ("no such file or directory", "Device or file not found — is the probe still plugged in?"),
    ("no symbol table", "No firmware symbols are loaded; this is usually harmless for flashing."),
    ("target is not attached", "Attach to a target first."),
)


def friendly_error(message: str) -> str:
    lowered = message.lower()
    for needle, friendly in _KNOWN:
        if needle in lowered:
            return friendly
    return message
