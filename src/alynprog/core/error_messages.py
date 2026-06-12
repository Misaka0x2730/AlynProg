"""Map raw GDB/probe error text to friendlier, actionable messages.

The substrings below were captured from real GDB + Black Magic Probe sessions and from live pyOCD
sessions (see ``docs/hardware-spike.md`` / ``docs/pyocd-spike.md``). Unknown errors pass through
unchanged so no information is lost.
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
    # --- pyOCD (native backend), captured from live sessions ---
    (
        "not recognized",
        "Unknown target type — choose a target from the list. If your chip needs a CMSIS pack, "
        "install it with 'pyocd pack install <part>' and click Refresh.",
    ),
    (
        "flash program page failure",
        "Flash programming failed. Make sure the selected target matches your exact chip "
        "(e.g. medium- vs high-density), then try again.",
    ),
    (
        "was not halted as expected",
        "The flash routine didn't run — the selected target likely doesn't match your chip. "
        "Pick the correct target and retry.",
    ),
    ("flash erase failure", "Flash erase failed — check the selected target matches your chip."),
    ("no connected debug probes", "No debug probe found — is it plugged in?"),
)


def friendly_error(message: str) -> str:
    lowered = message.lower()
    for needle, friendly in _KNOWN:
        if needle in lowered:
            return friendly
    return message
