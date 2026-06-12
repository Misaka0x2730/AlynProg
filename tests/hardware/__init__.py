"""Hardware-in-the-loop tests (require a real Black Magic Probe).

These are tagged ``@pytest.mark.hardware`` and skip themselves when no probe/GDB is available, so a
normal ``uv run pytest`` stays green. Run them explicitly with ``uv run pytest -m hardware`` against
a connected probe. Override discovery with ``ALYNPROG_BMP_PORT`` and the GDB binary with
``ALYNPROG_GDB`` if needed.
"""
