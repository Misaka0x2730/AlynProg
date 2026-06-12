"""Tests for the theme manager.

Note: under the offscreen QPA platform ``setColorScheme`` is a no-op (the effective scheme stays
Unknown), so we assert mode tracking and signal emission rather than the rendered scheme value.
"""

from __future__ import annotations

from alynprog.ui.theme import ThemeManager, ThemeMode


def test_apply_tracks_mode_and_emits(qapp):
    manager = ThemeManager(qapp)
    seen: list[ThemeMode] = []
    manager.changed.connect(seen.append)

    for mode in (ThemeMode.DARK, ThemeMode.LIGHT, ThemeMode.SYSTEM):
        manager.apply(mode)
        assert manager.mode is mode

    assert seen == [ThemeMode.DARK, ThemeMode.LIGHT, ThemeMode.SYSTEM]
    assert manager.effective_scheme() in ("light", "dark")


def test_from_value_falls_back():
    assert ThemeMode.from_value(None, ThemeMode.SYSTEM) is ThemeMode.SYSTEM
    assert ThemeMode.from_value("dark", ThemeMode.SYSTEM) is ThemeMode.DARK
    assert ThemeMode.from_value("bogus", ThemeMode.LIGHT) is ThemeMode.LIGHT


def test_style_is_fusion(qapp):
    ThemeManager(qapp)
    assert qapp.style().objectName() == "fusion"
