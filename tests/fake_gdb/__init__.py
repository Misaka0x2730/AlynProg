"""Hardware-free GDB/MI test harness.

``fake_gdb.py`` is a standalone Python script that speaks just enough of the GDB/MI protocol to
drive :class:`alynprog.transports.gdbmi.GdbMiSession` and the Black Magic Probe backend through
their full flows. It is launched as a subprocess by the integration tests via
:data:`sys.executable`, so the tests never need a real GDB or probe.
"""

from pathlib import Path

FAKE_GDB = Path(__file__).resolve().parent / "fake_gdb.py"
