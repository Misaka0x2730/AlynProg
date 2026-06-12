"""Backend registry: maps a backend name to its :class:`ProbeBackend` class.

Discovery iterates registered backends, calling each one's ``discover()``. Keeping registration
explicit (rather than import-time magic) makes the available backends obvious and testable.
"""

from __future__ import annotations

from alynprog.backends.bmp.backend import BmpBackend
from alynprog.backends.fake.backend import FakeBackend
from alynprog.backends.pyocd.backend import PyocdBackend
from alynprog.core.backend import ProbeBackend, ProbeInfo

_REGISTRY: dict[str, type[ProbeBackend]] = {}


def register(backend_cls: type[ProbeBackend]) -> None:
    _REGISTRY[backend_cls.name] = backend_cls


def backend_class(name: str) -> type[ProbeBackend]:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"no backend registered as {name!r}") from None


def registered_backends() -> dict[str, type[ProbeBackend]]:
    return dict(_REGISTRY)


def discover_all(*, include_fake: bool = False) -> list[ProbeInfo]:
    """Discover probes across registered backends.

    The simulated ``fake`` backend is excluded unless *include_fake* is set (the app passes
    ``--fake`` through to this).
    """
    probes: list[ProbeInfo] = []
    for name, cls in _REGISTRY.items():
        if name == "fake" and not include_fake:
            continue
        try:
            probes.extend(cls.discover())
        except Exception:
            continue
    return probes


register(BmpBackend)
register(FakeBackend)
register(PyocdBackend)
