"""Application entry point."""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from mqs import __version__
from mqs.logging_setup import SecretRegistry, setup_logging
from mqs.ui import strings_cs as S
from mqs.ui.main_window import MainWindow
from mqs.ui.theme import apply_theme, watch_theme


def resource_path(relative: str) -> Path:
    """Path to bundled resources, both from source and from a PyInstaller onefile build."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / relative


def main(argv: Sequence[str] | None = None) -> int:
    app = QApplication(list(argv) if argv is not None else sys.argv)
    app.setApplicationName(S.APP_TITLE)
    app.setApplicationVersion(__version__)
    icon = resource_path("assets/icon.ico")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    registry = SecretRegistry()
    log_file = setup_logging(registry)
    logging.getLogger("mqs").info("MikroTik QuickSet Plus %s started, log %s", __version__,
                                  log_file)

    window = MainWindow(registry=registry, dark=apply_theme(app))
    watch_theme(app, window.set_dark)
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
