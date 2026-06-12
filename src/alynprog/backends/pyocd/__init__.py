"""pyOCD backend (optional dependency).

This package is import-safe even when the ``pyocd`` package is not installed: nothing here imports
pyocd at module load time. The library is touched only inside :meth:`PyocdApi.real`, so the rest of
the application is unaffected when the optional dependency is absent.
"""

from alynprog.backends.pyocd._api import PyocdApi, pyocd_available

__all__ = ["PyocdApi", "pyocd_available"]
