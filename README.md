# AlynProg

Cross-platform desktop utility (Windows, Linux, macOS) for flashing firmware into
microcontrollers and inspecting their memory.

What it can do:

- Connect to a [Black Magic Probe](https://black-magic.org/), scan for targets and attach —
  driven through `arm-none-eabi-gdb` (GDB/MI).
- View **and edit** target memory in a hex table.
- Flash ELF / HEX / BIN firmware images; full and sector erase.
- Live operation log.
- Light / dark / system themes (PySide6 / Qt 6 UI).
- Probe abstraction designed for more backends later (OpenOCD, pyOCD).

> Status: early development.

## Requirements

- Python ≥ 3.11
- [`uv`](https://docs.astral.sh/uv/) (recommended) — or any PEP 517 frontend
- An `arm-none-eabi-gdb` (or `gdb-multiarch`) on your `PATH`, or point AlynProg at one in
  *Preferences*. The [xPack Arm Embedded GCC](https://xpack-dev-tools.github.io/arm-none-eabi-gcc-xpack/)
  builds bundle a suitable GDB for all supported OSes.

## Running

```sh
uv run alynprog
```

To explore the UI without any hardware (uses a built-in simulated target):

```sh
uv run alynprog --fake
```

### Linux: USB access for Black Magic Probe

Install the bundled udev rules so non-root users can talk to the probe:

```sh
sudo cp src/alynprog/resources/99-blackmagic.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

## License

MIT (see [LICENSE](LICENSE)).
