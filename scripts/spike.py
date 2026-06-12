#!/usr/bin/env python3
"""Milestone-M3 hardware spike: drive a real Black Magic Probe and capture MI transcripts.

This is a developer diagnostic, not part of the app. It exercises the risky assumptions (port
discovery, dialect detection, memory map quality, read throughput, flash-write error text, ELF/HEX/
BIN flashing, compare-sections, erase availability) and prints everything, including the raw GDB/MI
traffic, so the captured output can become golden fixtures for the fake-gdb scenarios.

Usage:
    uv run python scripts/spike.py [--port /dev/ttyACM0] [--gdb /path/to/arm-none-eabi-gdb]
                                   [--elf firmware.elf] [--bin firmware.bin --base 0x08000000]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running directly from a checkout without installing.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from alynprog.backends.bmp.backend import BmpBackend
from alynprog.backends.bmp.discovery import discover_bmp_probes
from alynprog.core.backend import (
    ConnectOptions,
    EraseKind,
    EraseSpec,
    ProbeInfo,
)
from alynprog.core.image import FirmwareImage
from alynprog.tools.gdb_locator import find_gdb


def banner(title: str) -> None:
    print(f"\n{'=' * 4} {title} {'=' * (72 - len(title))}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="serial device of the probe's GDB port")
    parser.add_argument("--gdb", help="path to arm-none-eabi-gdb (else auto-detected)")
    parser.add_argument("--elf", help="optional ELF to flash-test")
    parser.add_argument("--bin", help="optional raw BIN to flash-test")
    parser.add_argument("--base", help="base address for --bin (e.g. 0x08000000)")
    parser.add_argument("--tpwr", action="store_true", help="enable target power")
    args = parser.parse_args()

    gdb = find_gdb(args.gdb)
    if gdb is None:
        print("ERROR: no GDB found; pass --gdb", file=sys.stderr)
        return 2
    print(f"GDB: {gdb.path} ({gdb.version})")

    if args.port:
        probe = ProbeInfo(backend="bmp", identifier=args.port, display_name=f"BMP ({args.port})")
    else:
        probes = discover_bmp_probes()
        for p in probes:
            print(f"discovered: {p.display_name} -> {p.identifier} (ambiguous={p.port_ambiguous})")
        if not probes:
            print("ERROR: no probe discovered; pass --port", file=sys.stderr)
            return 2
        probe = probes[0]

    backend = BmpBackend(
        gdb_path=gdb.path,
        log_callback=lambda stream, text: print(f"  [{stream}] {text}"),
    )

    banner("connect")
    backend.connect(probe, ConnectOptions(target_power=args.tpwr))
    dialect = backend._require_dialect()
    print(f"scan command: monitor {dialect.scan_command('swd')}")
    print(f"capabilities: {backend.capabilities()}")

    banner("scan")
    targets = backend.scan_targets()
    for t in targets:
        print(f"target {t.index}: {t.name}")
    if not targets:
        print("no targets; stopping")
        backend.close()
        return 1

    banner("attach + memory map")
    backend.attach(targets[0].index)
    for region in backend.memory_map():
        print(f"  {region.kind.value:7} 0x{region.start:08x} size 0x{region.size:x}")

    banner("read flash")
    flash = next((r for r in backend.memory_map() if r.kind.value == "flash"), None)
    if flash:
        print("first 32 bytes:", backend.read_memory(flash.start, 32).hex())

    banner("flash write error capture")
    if flash:
        try:
            backend.write_memory(flash.start, b"\x00")
            print("  (unexpected) flash write succeeded")
        except Exception as exc:
            print(f"  flash write raised: {type(exc).__name__}: {exc}")

    if args.elf:
        banner("flash ELF")
        _flash(backend, FirmwareImage.load(args.elf))
    if args.bin:
        banner("flash BIN")
        base = int(args.base, 0) if args.base else None
        _flash(backend, FirmwareImage.load(args.bin, base_addr=base))

    banner("erase availability")
    try:
        backend.erase(EraseSpec(kind=EraseKind.MASS))
        print("  mass erase OK")
    except Exception as exc:
        print(f"  mass erase: {type(exc).__name__}: {exc}")

    backend.close()
    print("\nspike complete")
    return 0


def _flash(backend: BmpBackend, image: FirmwareImage) -> None:
    result = backend.program(
        image,
        progress=lambda done, total: print(f"  download {done}/{total}"),
        verify=True,
    )
    print(f"  result: {result}")


if __name__ == "__main__":
    raise SystemExit(main())
