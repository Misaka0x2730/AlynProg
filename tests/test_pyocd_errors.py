"""M1: pyOCD exception -> AlynProg error mapping (skipped when pyocd is not installed)."""

from __future__ import annotations

import pytest

from alynprog.backends.pyocd._api import pyocd_available
from alynprog.backends.pyocd.errors import mapped_errors
from alynprog.core.errors import ImageError, ProbeError, TargetError

pytestmark = pytest.mark.skipif(not pyocd_available(), reason="pyocd not installed")


def _raise_in_context(exc: BaseException, action: str = "test op") -> None:
    with mapped_errors(action):
        raise exc


def test_probe_errors_map_to_probe_error():
    from pyocd.core import exceptions as px

    for cls in (px.ProbeError, px.ProbeDisconnected):
        with pytest.raises(ProbeError):
            _raise_in_context(cls("boom"))


@pytest.mark.parametrize(
    "name",
    [
        "TargetError",
        "TargetSupportError",
        "DebugError",
        "CoreRegisterAccessError",
        "TransferError",
        "TransferFaultError",
        "TimeoutError",
        "FlashFailure",
        "FlashEraseFailure",
        "FlashProgramFailure",
    ],
)
def test_target_and_flash_errors_map_to_target_error(name):
    from pyocd.core import exceptions as px

    cls = getattr(px, name)
    with pytest.raises(TargetError):
        _raise_in_context(cls("boom"))


def test_base_pyocd_error_maps_to_probe_error():
    from pyocd.core import exceptions as px

    with pytest.raises(ProbeError):
        _raise_in_context(px.Error("generic failure"))


def test_value_error_maps_to_image_error():
    with pytest.raises(ImageError):
        _raise_in_context(ValueError("unknown file format"))


def test_file_not_found_maps_to_image_error():
    with pytest.raises(ImageError):
        _raise_in_context(FileNotFoundError("missing.hex"))


def test_action_label_is_in_message():
    from pyocd.core import exceptions as px

    with pytest.raises(TargetError, match="flashing"):
        _raise_in_context(px.TargetError("device fell off"), action="flashing")


def test_non_pyocd_error_propagates_unchanged():
    with pytest.raises(KeyError):
        _raise_in_context(KeyError("not ours"))


def test_already_mapped_alynprog_error_passes_through():
    with pytest.raises(TargetError):
        _raise_in_context(TargetError("already ours"))
