"""Locate a usable GDB executable for the Black Magic Probe backend.

Search order: an explicit configured path → known toolchain executables on ``PATH``
(``arm-none-eabi-gdb``, ``gdb-multiarch``, ``gdb``) → a few well-known per-OS install locations.
A candidate is accepted only if ``<gdb> --version`` runs and reports a GDB banner. The executable
is never hard-coded; the path is configurable in Preferences and persisted in settings.
"""

from __future__ import annotations

import glob
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Preference order: an Arm cross GDB first (it understands Cortex-M out of the box), then a
# multiarch GDB, then a plain gdb (which may still work for many targets).
CANDIDATE_NAMES = ("arm-none-eabi-gdb", "gdb-multiarch", "gdb")


@dataclass(frozen=True)
class GdbInfo:
    path: str
    version: str


def validate_gdb(path: str | Path) -> GdbInfo | None:
    """Return :class:`GdbInfo` if *path* runs and looks like GDB, else ``None``."""
    try:
        proc = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = f"{proc.stdout}\n{proc.stderr}"
    if proc.returncode == 0 and "gdb" in output.lower():
        first_line = output.strip().splitlines()[0] if output.strip() else "GDB"
        return GdbInfo(path=str(path), version=first_line.strip())
    return None


def _known_locations() -> list[str]:
    """Best-effort, OS-specific install locations (globs allowed)."""
    patterns: list[str] = []
    if sys.platform == "darwin":
        patterns += [
            "/Applications/ArmGNUToolchain/*/*/bin/arm-none-eabi-gdb",
            "/opt/homebrew/bin/arm-none-eabi-gdb",
            "/opt/homebrew/bin/gdb-multiarch",
            "/usr/local/bin/arm-none-eabi-gdb",
        ]
    elif sys.platform.startswith("linux"):
        patterns += [
            "/usr/bin/arm-none-eabi-gdb",
            "/usr/bin/gdb-multiarch",
            "/opt/*/bin/arm-none-eabi-gdb",
        ]
    elif sys.platform.startswith("win"):
        patterns += [
            r"C:\Program Files (x86)\Arm GNU Toolchain*\*\bin\arm-none-eabi-gdb.exe",
            r"C:\Program Files\Arm GNU Toolchain*\*\bin\arm-none-eabi-gdb.exe",
        ]
    results: list[str] = []
    for pattern in patterns:
        results.extend(sorted(glob.glob(pattern)))
    return results


def find_gdb(explicit_path: str | None = None) -> GdbInfo | None:
    """Find a usable GDB, honouring *explicit_path* first."""
    if explicit_path:
        info = validate_gdb(explicit_path)
        if info is not None:
            return info
        # An explicit but invalid path is a hard miss — do not silently fall back.
        return None

    for name in CANDIDATE_NAMES:
        found = shutil.which(name)
        if found:
            info = validate_gdb(found)
            if info is not None:
                return info

    for location in _known_locations():
        info = validate_gdb(location)
        if info is not None:
            return info

    return None
