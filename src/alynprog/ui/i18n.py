"""Translation loading.

Source strings live in English in the code (wrapped in ``tr()``). Translations are compiled from
``resources/i18n/alynprog_<locale>.ts`` into ``.qm`` files (via ``pyside6-lrelease``) and loaded
here at startup. Missing translations are not an error — the English source is used.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
from PySide6.QtWidgets import QApplication

_I18N_DIR = Path(__file__).resolve().parent.parent / "resources" / "i18n"

# Keep references so the translators are not garbage-collected after install.
_installed: list[QTranslator] = []


def install_translators(app: QApplication, locale: QLocale | None = None) -> None:
    """Install application and Qt-base translators for *locale* (defaults to the system locale)."""
    loc = locale or QLocale.system()

    app_tr = QTranslator(app)
    if app_tr.load(loc, "alynprog", "_", str(_I18N_DIR)):
        app.installTranslator(app_tr)
        _installed.append(app_tr)

    # Qt's own standard dialog/button strings.
    qt_tr = QTranslator(app)
    qt_path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if qt_tr.load(loc, "qtbase", "_", qt_path):
        app.installTranslator(qt_tr)
        _installed.append(qt_tr)
