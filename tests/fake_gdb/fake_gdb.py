#!/usr/bin/env python3
"""A scripted fake GDB that speaks GDB/MI over stdin/stdout.

It serves two purposes:

* **Transport mechanics** — synthetic ``-test-*`` commands exercise token correlation, timeouts,
  interleaved async records, error results and abrupt death, independent of any target semantics.
* **Black Magic Probe simulation** — enough of ``monitor``/memory/download commands to drive the
  BMP backend end-to-end, parameterised by ``--profile`` to emulate different firmware variants.

The script ignores the standard gdb flags (``-nx -q --interpreter=mi3``) so it can stand in for a
real gdb, but tests normally launch it via an ``argv_override`` of
``[sys.executable, fake_gdb.py, --profile, …]``.
"""

from __future__ import annotations

import argparse
import sys
import time

FLASH_BASE = 0x0800_0000
FLASH_SIZE = 0x0010_0000  # 1 MiB
RAM_BASE = 0x2000_0000
RAM_SIZE = 0x0002_0000  # 128 KiB

# Each profile: version, general (always-listed) commands, target-specific commands (listed in
# 'monitor help' only AFTER attach, like a real BMP), and accepted scan aliases.
PROFILES = {
    "bmp-v1": {
        "version": "Black Magic Probe (Firmware v1.10.2)",
        "general": ["swdp_scan", "jtag_scan", "reset", "frequency", "tpwr", "connect_rst"],
        "target": ["erase_mass"],
        "scan_aliases": ["swdp_scan"],
    },
    "bmp-v2": {
        "version": "Black Magic Probe (Firmware v2.0.0)",
        "general": [
            "swd_scan",
            "swdp_scan",
            "jtag_scan",
            "reset",
            "frequency",
            "tpwr",
            "connect_rst",
        ],
        "target": ["erase_mass", "erase_range"],
        "scan_aliases": ["swd_scan", "swdp_scan"],
    },
    "bmp-no-erase": {
        "version": "Black Magic Probe (Firmware v2.0.0)",
        "general": ["swdp_scan", "jtag_scan", "reset", "frequency", "tpwr"],
        "target": [],
        "scan_aliases": ["swdp_scan"],
    },
    # Mirrors a real captured BMP v2.0.0 (Pico): swd_scan primary, swdp_scan deprecated alias, and
    # erase_mass/erase_range present only as target-specific commands (i.e. after attach).
    "bmp-real-v2": {
        "version": "Black Magic Probe (Pico, auto-detect), BMP v2.0.0-582, Hardware Version 4",
        "general": [
            "version",
            "help",
            "jtag_scan",
            "swd_scan",
            "swdp_scan",
            "frequency",
            "reset",
            "connect_rst",
            "tpwr",
        ],
        "target": ["erase_mass", "erase_range"],
        "scan_aliases": ["swd_scan", "swdp_scan"],
    },
}


def c_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")


class FakeGdb:
    def __init__(self, profile: str) -> None:
        spec = PROFILES[profile]
        self.version = spec["version"]
        self.general_cmds = spec["general"]
        self.target_cmds = spec["target"]
        self.scan_aliases = spec["scan_aliases"]
        self.out = sys.stdout
        self.attached = False
        self.running = False
        self.ram_overlay: dict[int, int] = {}
        self.flash_overlay: dict[int, int] = {}  # bytes written via the flash (download) path
        self.exec_file: str | None = None

    # --- low-level emit --------------------------------------------------------

    def emit(self, line: str) -> None:
        self.out.write(line + "\n")
        self.out.flush()

    def console(self, text: str) -> None:
        self.emit(f'~"{c_escape(text)}"')

    def target(self, text: str) -> None:
        # Remote 'monitor' output is relayed by GDB as target-stream (@) records, not console (~).
        self.emit(f'@"{c_escape(text)}"')

    def result(self, token: str, cls: str, payload: str = "") -> None:
        body = f"{token}^{cls}"
        if payload:
            body += "," + payload
        self.emit(body)

    # --- main loop -------------------------------------------------------------

    def run(self) -> None:
        self.emit('=thread-group-added,id="i1"')
        self.emit("(gdb)")
        for raw in sys.stdin:
            line = raw.rstrip("\r\n")
            if not line:
                continue
            token, command = _split_token(line)
            if not self.handle(token, command):
                return  # -gdb-exit / -test-die

    def handle(self, token: str, command: str) -> bool:
        # Synthetic transport-test hooks.
        if command == "-test-echo":
            self.result(token, "done", f'echo="{token}"')
        elif command.startswith("-test-slow"):
            seconds = _arg_float(command, default=2.0)
            time.sleep(seconds)
            self.result(token, "done")
        elif command == "-test-die":
            self.out.flush()
            return False
        elif command.startswith("-test-error"):
            msg = command[len("-test-error") :].strip() or "synthetic error"
            self.result(token, "error", f'msg="{c_escape(msg)}"')
        elif command.startswith("-test-async"):
            count = int(_arg_float(command, default=3.0))
            for i in range(count):
                self.emit(f'=tick,n="{i}"')
            self.result(token, "done")
        # GDB/MI commands used by the BMP backend.
        elif command.startswith("-gdb-set") or command.startswith("-gdb-unset"):
            self.result(token, "done")
        elif command == "-gdb-exit":
            self.result(token, "exit")
            self.out.flush()
            return False
        elif command.startswith("-target-select"):
            self.console("Remote debugging using fake-port\n")
            self.result(token, "connected")
        elif command.startswith("-target-attach") or command.startswith("attach"):
            self.attached = True
            self.result(token, "done")
        elif command.startswith("-target-detach") or command == "detach":
            self.attached = False
            self.result(token, "done")
        elif command.startswith("-interpreter-exec console"):
            self.handle_console(token, _console_arg(command))
        elif command.startswith("-data-read-memory-bytes"):
            self.handle_read(token, command)
        elif command.startswith("-data-write-memory-bytes"):
            self.handle_write(token, command)
        elif command.startswith("-file-exec-and-symbols"):
            self.exec_file = _unquote_last_arg(command)
            self.result(token, "done")
        elif command.startswith("-target-download"):
            self.handle_download(token)
        elif command.startswith("-data-list-register-names"):
            names = ",".join(f'"{n}"' for n in _REGISTER_NAMES)
            self.result(token, "done", f"register-names=[{names}]")
        elif command.startswith("-data-list-register-values"):
            values = ",".join(
                f'{{number="{i}",value="0x{i * 0x11:08x}"}}' for i in range(len(_REGISTER_NAMES))
            )
            self.result(token, "done", f"register-values=[{values}]")
        elif command.startswith("-exec-continue") or command.startswith("-exec-run"):
            self.running = True
            self.emit('*running,thread-id="all"')
            self.result(token, "running")
        elif command.startswith("-exec-interrupt"):
            if not self.running:
                self.result(token, "error", 'msg="Cannot interrupt: target not running"')
            else:
                self.running = False
                self.emit('*stopped,reason="signal-received"')
                self.result(token, "done")
        else:
            # Unknown but harmless: acknowledge so the session does not stall.
            self.result(token, "done")
        return True

    # --- console / monitor -----------------------------------------------------

    def handle_console(self, token: str, inner: str) -> None:
        if inner.startswith("attach"):
            self.attached = True
            self.console("Attaching to Remote target\n")
            self.result(token, "done")
            return
        if inner == "info mem":
            self.console("Using memory regions provided by the target.\n")
            self.console("Num Enb Low Addr   High Addr  Attrs\n")
            self.console(
                f"0   y  0x{FLASH_BASE:08x} 0x{FLASH_BASE + FLASH_SIZE:08x} flash blocksize 0x800\n"
            )
            self.console(f"1   y  0x{RAM_BASE:08x} 0x{RAM_BASE + RAM_SIZE:08x} rw\n")
            self.result(token, "done")
            return
        if inner.startswith("monitor "):
            self.handle_monitor(token, inner[len("monitor ") :].strip())
            return
        if inner.startswith("compare-sections"):
            self.console("Section .text, range 0x08000000 -- 0x08000400: matched.\n")
            self.result(token, "done")
            return
        self.result(token, "done")

    def handle_monitor(self, token: str, mon: str) -> None:
        # Real BMP relays monitor output via the target stream (@), not the console stream (~).
        word = mon.split()[0] if mon else ""
        if word == "version":
            self.target(self.version + "\n")
            self.result(token, "done")
        elif word == "help":
            self.target("General commands:\n")
            for cmd in self.general_cmds:
                self.target(f"\t{cmd} -- ...\n")
            # Target-specific commands are registered by the target driver, so they only appear
            # in 'monitor help' after a successful attach (matching real BMP behaviour).
            if self.attached and self.target_cmds:
                self.target("Target specific commands:\n")
                for cmd in self.target_cmds:
                    self.target(f"\t{cmd} -- ...\n")
            self.result(token, "done")
        elif word in self.scan_aliases or word in ("swdp_scan", "swd_scan", "jtag_scan"):
            if word not in self.scan_aliases:
                self.target(f"Unknown command: {word}\n")
                self.result(token, "error", 'msg="Unsupported monitor command"')
                return
            self.target("Target voltage: 3.3V\n")
            self.target("Available Targets:\n")
            self.target("No. Att Driver\n")
            self.target(" 1      STM32F40x M4\n")
            self.target(" 2      STM32F40x M4\n")
            self.result(token, "done")
        elif word == "reset":
            self.target("\n")
            self.result(token, "done")
        elif word in ("erase_mass", "erase_range"):
            if self.attached and word in self.target_cmds:
                self.target("Erasing flash... done\n")
                self.result(token, "done")
            else:
                self.result(token, "error", 'msg="Unsupported monitor command"')
        elif word in ("tpwr", "connect_rst", "connect_srst"):
            self.target(f"{word} enabled\n")
            self.result(token, "done")
        else:
            self.result(token, "done")

    # --- memory ----------------------------------------------------------------

    def _read_byte(self, addr: int) -> int:
        if addr in self.ram_overlay:
            return self.ram_overlay[addr]
        if addr in self.flash_overlay:
            return self.flash_overlay[addr]
        return addr & 0xFF

    def handle_read(self, token: str, command: str) -> None:
        parts = command.split()
        try:
            addr = int(parts[1], 0)
            count = int(parts[2], 0)
        except (IndexError, ValueError):
            self.result(token, "error", 'msg="bad -data-read-memory-bytes arguments"')
            return
        data = bytes(self._read_byte(addr + i) for i in range(count))
        end = addr + count
        payload = (
            f'memory=[{{begin="0x{addr:x}",offset="0x0",end="0x{end:x}",contents="{data.hex()}"}}]'
        )
        self.result(token, "done", payload)

    def handle_write(self, token: str, command: str) -> None:
        parts = command.split()
        try:
            addr = int(parts[1], 0)
            contents = parts[2]
            data = bytes.fromhex(contents)
        except (IndexError, ValueError):
            self.result(token, "error", 'msg="bad -data-write-memory-bytes arguments"')
            return
        if FLASH_BASE <= addr < FLASH_BASE + FLASH_SIZE:
            self.result(
                token, "error", 'msg="Cannot write to read-only/flash memory at this address"'
            )
            return
        for i, value in enumerate(data):
            self.ram_overlay[addr + i] = value
        self.result(token, "done")

    def handle_download(self, token: str) -> None:
        # Apply the current exec file (an Intel-HEX) to the flash overlay, so reads after a flash
        # program / RMW reflect the new contents — exactly like a real flash write.
        written = self._apply_ihex(self.exec_file) if self.exec_file else 0
        total = written or 4096
        self.emit(f'+download,{{section=".text",section-size="{total}",total-size="{total}"}}')
        self.emit(
            f'+download,{{section=".text",section-sent="{total}",section-size="{total}",'
            f'total-sent="{total}",total-size="{total}"}}'
        )
        self.result(token, "done")

    def _apply_ihex(self, path: str) -> int:
        """Parse a minimal Intel-HEX file into the flash overlay; return the byte count written."""
        upper = 0
        written = 0
        try:
            with open(path) as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line.startswith(":"):
                        continue
                    record = bytes.fromhex(line[1:])
                    count, hi, lo, rtype = record[0], record[1], record[2], record[3]
                    payload = record[4 : 4 + count]
                    if rtype == 0x00:  # data
                        full = (upper << 16) + (hi << 8) + lo
                        for i, value in enumerate(payload):
                            self.flash_overlay[full + i] = value
                            written += 1
                    elif rtype == 0x04:  # extended linear address
                        upper = (payload[0] << 8) | payload[1]
                    elif rtype == 0x01:  # EOF
                        break
        except OSError:
            pass
        return written


_REGISTER_NAMES = [
    "r0",
    "r1",
    "r2",
    "r3",
    "r4",
    "r5",
    "r6",
    "r7",
    "r8",
    "r9",
    "r10",
    "r11",
    "r12",
    "sp",
    "lr",
    "pc",
    "xpsr",
]


def _split_token(line: str) -> tuple[str, str]:
    i = 0
    while i < len(line) and line[i].isdigit():
        i += 1
    return line[:i], line[i:]


def _arg_float(command: str, *, default: float) -> float:
    parts = command.split()
    if len(parts) >= 2:
        try:
            return float(parts[1])
        except ValueError:
            return default
    return default


def _unquote_last_arg(command: str) -> str:
    """Return the (optionally quoted) last argument of a command line."""
    if '"' in command:
        start = command.find('"')
        end = command.rfind('"')
        if end > start:
            return command[start + 1 : end]
    return command.split()[-1] if command.split() else ""


def _console_arg(command: str) -> str:
    # command == '-interpreter-exec console "<inner>"'
    start = command.find('"')
    end = command.rfind('"')
    if start == -1 or end <= start:
        return ""
    inner = command[start + 1 : end]
    return inner.replace('\\"', '"').replace("\\\\", "\\")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=list(PROFILES), default="bmp-v2")
    args, _ignored = parser.parse_known_args()
    FakeGdb(args.profile).run()


if __name__ == "__main__":
    main()
