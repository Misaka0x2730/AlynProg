"""Core, UI-independent logic: probe abstraction, transports glue, session control.

Modules in this package depend only on the Python standard library and (for the session/worker
layer) PySide6's QtCore — never on QtWidgets. This keeps the same code reusable from a future
headless CLI.
"""
