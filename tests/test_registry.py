"""Tests for the backend registry."""

from __future__ import annotations

from alynprog.backends.bmp.backend import BmpBackend
from alynprog.backends.fake.backend import FakeBackend
from alynprog.core.registry import backend_class, discover_all, registered_backends


def test_bmp_and_fake_are_registered():
    names = registered_backends()
    assert names["bmp"] is BmpBackend
    assert names["fake"] is FakeBackend


def test_backend_class_lookup_and_miss():
    assert backend_class("fake") is FakeBackend
    try:
        backend_class("nonexistent")
    except KeyError as exc:
        assert "nonexistent" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected KeyError")


def test_discover_excludes_fake_by_default():
    # Real BMP discovery returns [] without hardware; fake is hidden unless requested.
    assert all(p.backend != "fake" for p in discover_all())
    assert any(p.backend == "fake" for p in discover_all(include_fake=True))
