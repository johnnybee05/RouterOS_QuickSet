"""Follow the Windows light/dark setting (Qt 6 color scheme hints)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

ERROR_RED = "#d32f2f"
WARNING_ORANGE = "#ef6c00"
OK_GREEN = "#2e7d32"

STYLESHEET = f"""
*[fieldError="true"] {{ border: 1px solid {ERROR_RED}; border-radius: 2px; }}
QLabel[role="fieldMessage"] {{ color: {ERROR_RED}; }}
QLabel[role="badge"] {{ font-weight: bold; padding: 2px 8px; border-radius: 4px; }}
QLabel[role="danger"] {{ color: {ERROR_RED}; font-weight: bold; }}
QLabel[role="ok"] {{ color: {OK_GREEN}; }}
"""


def is_dark(app: QGuiApplication) -> bool:
    return app.styleHints().colorScheme() == Qt.ColorScheme.Dark


def dark_palette() -> QPalette:
    palette = QPalette()
    base, window, text = QColor(30, 30, 30), QColor(45, 45, 45), QColor(230, 230, 230)
    disabled = QColor(127, 127, 127)
    palette.setColor(QPalette.ColorRole.Window, window)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, base)
    palette.setColor(QPalette.ColorRole.AlternateBase, window)
    palette.setColor(QPalette.ColorRole.ToolTipBase, window)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, window)
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.Link, QColor(100, 170, 255))
    palette.setColor(QPalette.ColorRole.PlaceholderText, disabled)
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText,
                 QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    return palette


def apply_theme(app: QApplication) -> bool:
    app.setStyle("Fusion")
    dark = is_dark(app)
    app.setPalette(dark_palette() if dark else app.style().standardPalette())
    app.setStyleSheet(STYLESHEET)
    return dark


def watch_theme(app: QApplication, on_change: Callable[[bool], None]) -> None:
    def changed(_scheme: Qt.ColorScheme) -> None:
        on_change(apply_theme(app))

    app.styleHints().colorSchemeChanged.connect(changed)
