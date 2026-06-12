"""Application entry point: argument parsing, QApplication bootstrap, theming, i18n."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from PySide6.QtWidgets import QApplication

from alynprog import APP_NAME, APP_ORG, __version__


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="alynprog", description=f"{APP_NAME} — microcontroller flasher"
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    parser.add_argument(
        "--fake",
        action="store_true",
        help="use the built-in simulated target backend (no hardware required)",
    )
    parser.add_argument(
        "--theme",
        choices=["light", "dark", "system"],
        default=None,
        help="override the startup theme",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    app = QApplication(sys.argv if argv is None else [sys.argv[0], *argv])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORG)
    app.setApplicationDisplayName(APP_NAME)

    # Imports are deferred so that --version/--help do not pay the Qt widget import cost and so
    # that QApplication exists before any widget/translation machinery is constructed.
    from alynprog.core.settings import Settings
    from alynprog.ui.i18n import install_translators
    from alynprog.ui.main_window import MainWindow
    from alynprog.ui.theme import ThemeManager, ThemeMode

    settings = Settings()
    install_translators(app)

    theme = ThemeManager(app)
    startup_mode = ThemeMode.from_value(args.theme or settings.theme_mode, ThemeMode.SYSTEM)
    theme.apply(startup_mode)

    window = MainWindow(theme=theme, settings=settings, use_fake=args.fake)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
