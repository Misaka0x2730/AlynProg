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
- Optional **pyOCD** backend (native, in-process) for CMSIS-DAP / ST-Link / J-Link / picoprobe — no
  external GDB needed. Pick the target from a searchable dialog sourced from pyOCD's own database.
- Probe abstraction designed for more backends later (OpenOCD).

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

### pyOCD backend (optional)

For CMSIS-DAP / ST-Link / J-Link / picoprobe debuggers, install the optional `pyocd` extra:

```sh
uv sync --extra pyocd        # or: pip install 'alynprog[pyocd]'
```

Use **Python 3.11–3.13** for this extra: `libusb-package` has no cp314 wheel yet, so on 3.14 it is
built from source (needs a C toolchain). On Linux, CMSIS-DAP probes need udev rules (see
[pyOCD's docs](https://pyocd.io/docs/installing.html)).

pyOCD can't auto-detect the chip, so after selecting a pyOCD probe you choose the **target** from a
searchable dialog (filter by vendor and family). The list comes from pyOCD's own database. If your
chip isn't listed, install its CMSIS pack and click **Refresh**:

```sh
uv run pyocd pack install stm32f103cb   # example: medium-density STM32F103 (blue pill)
```

Pick the target that matches your exact part — e.g. a common blue-pill is *medium-density*
`stm32f103cb`, not the high-density `stm32f103rc` that ships builtin; a mismatch fails to program.

### Linux: USB access for Black Magic Probe

Install the bundled udev rules so non-root users can talk to the probe:

```sh
sudo cp src/alynprog/resources/99-blackmagic.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

## License

MIT (see [LICENSE](LICENSE)).
