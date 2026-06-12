"""Translate pyOCD exceptions into AlynProg's exception hierarchy.

pyOCD is imported lazily (only when an actual failure needs classifying), so this module is
import-safe without the optional ``pyocd`` dependency. Exceptions that did not originate in pyOCD
are left untouched and propagate unchanged.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from alynprog.core.errors import AlynProgError, ImageError, ProbeError, TargetError

# pyOCD exception class *names* grouped by the AlynProg error they map to. Using names (resolved
# lazily) keeps this module pyocd-free at import time and tolerant of classes a given pyOCD version
# may not define. Note that several pyOCD errors already subclass ``TargetError`` (DebugError,
# CoreRegisterAccessError, the Transfer* family, FlashFailure and its subclasses), so isinstance
# covers them; the names below are listed for the direct-``Error`` subclasses and for clarity.
_PROBE_ERROR_NAMES = ("ProbeError", "ProbeDisconnected")
_TARGET_ERROR_NAMES = (
    "TargetError",
    "TargetSupportError",
    "DebugError",
    "CoreRegisterAccessError",
    "TransferError",
    "TransferTimeoutError",
    "TransferProtocolError",
    "TransferFaultError",
    "TimeoutError",  # pyOCD's own, not the builtin
    "FlashFailure",
    "FlashEraseFailure",
    "FlashProgramFailure",
)


@contextlib.contextmanager
def mapped_errors(action: str) -> Iterator[None]:
    """Run a pyOCD call, re-raising its exceptions as AlynProg errors tagged with *action*.

    *action* is a short human phrase ("connecting", "flashing", …) prefixed to the message so the
    UI's error panel reads naturally. AlynProg errors raised inside pass through unchanged; bad
    arguments from ``FileProgrammer`` (``ValueError``/``FileNotFoundError``) become
    :class:`ImageError`; anything not from pyOCD propagates as-is.
    """
    try:
        yield
    except AlynProgError:
        raise
    except FileNotFoundError as exc:
        raise ImageError(f"{action}: file not found: {exc}") from exc
    except ValueError as exc:
        raise ImageError(f"{action}: {exc}") from exc
    except Exception as exc:
        # Intentionally broad: classify pyOCD errors, re-raise everything else untouched.
        mapped = _translate(exc, action)
        if mapped is None:
            raise
        raise mapped from exc


def _translate(exc: BaseException, action: str) -> AlynProgError | None:
    """Map a pyOCD exception to an AlynProg error, or ``None`` if it is not a pyOCD error."""
    try:
        from pyocd.core import exceptions as px
    except ImportError:
        return None  # pyocd absent -> this exception cannot be one of its own
    base = getattr(px, "Error", None)
    if base is None or not isinstance(exc, base):
        return None
    message = f"{action}: {exc}"
    if _isinstance_of_names(exc, px, _PROBE_ERROR_NAMES):
        return ProbeError(message)
    if _isinstance_of_names(exc, px, _TARGET_ERROR_NAMES):
        return TargetError(message)
    # Base pyOCD ``Error`` with no finer classification: treat as a probe-level failure.
    return ProbeError(message)


def _isinstance_of_names(exc: BaseException, module: object, names: tuple[str, ...]) -> bool:
    classes = tuple(c for c in (getattr(module, n, None) for n in names) if isinstance(c, type))
    return isinstance(exc, classes)
