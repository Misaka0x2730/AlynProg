"""GDB/MI transport layer.

This package is intentionally free of any Qt dependency: it uses only the standard library plus
``pygdbmi``'s line parser. The single Qt boundary in the application lives in
``alynprog.core.worker``. Keeping the transport Qt-free lets it be unit-tested without an event
loop and reused from a future headless CLI.
"""
