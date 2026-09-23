"""All modal dialogs behind one object, so pages can be tested with a fake."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from mqs.issues import Issue
from mqs.ui import strings_cs as S
from mqs.ui.highlighter import RouterOsHighlighter
from mqs.ui.widgets.common import IssueList, monospace_font


class DryRunDialog(QDialog):
    def __init__(self, parent: QWidget | None, script: str, issues: Sequence[Issue],
                 dark: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle(S.DRY_RUN_TITLE)
        self.resize(900, 700)
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.script = QPlainTextEdit(script)
        self.script.setReadOnly(True)
        self.script.setFont(monospace_font())
        self.highlighter = RouterOsHighlighter(self.script.document(), dark)
        splitter.addWidget(self.script)
        self.issues = IssueList()
        self.issues.set_issues(issues)
        splitter.addWidget(self.issues)
        splitter.setStretchFactor(0, 3)
        layout.addWidget(splitter)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton(S.DRY_RUN_CONFIRM, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class ResetIdentityDialog(QDialog):
    def __init__(self, parent: QWidget | None, identity: str, *,
                 keep_users: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle(S.RESET_TITLE)
        layout = QVBoxLayout(self)
        users = S.RESET_USERS_KEPT if keep_users else S.RESET_USERS_DELETED
        text = QLabel(S.RESET_TEXT.format(identity=identity, users=users))
        text.setWordWrap(True)
        text.setProperty("role", "danger")
        layout.addWidget(text)
        self.input = QLineEdit()
        layout.addWidget(self.input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(False)
        self.input.textChanged.connect(lambda t: ok.setEnabled(t == identity))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class Dialogs:
    def error(self, parent: QWidget | None, text: str) -> None:
        QMessageBox.critical(parent, S.ERROR_TITLE, text)

    def info(self, parent: QWidget | None, text: str) -> None:
        QMessageBox.information(parent, S.APP_TITLE, text)

    def question(self, parent: QWidget | None, title: str, text: str) -> bool:
        return QMessageBox.question(parent, title, text) == QMessageBox.StandardButton.Yes

    def ask_text(self, parent: QWidget | None, title: str, label: str,
                 text: str = "") -> str | None:
        value, ok = QInputDialog.getText(parent, title, label, text=text)
        return value if ok else None

    def ask_item(self, parent: QWidget | None, title: str, label: str,
                 items: Sequence[str]) -> str | None:
        if not items:
            return None
        value, ok = QInputDialog.getItem(parent, title, label, list(items), 0, False)
        return value if ok else None

    def open_file(self, parent: QWidget | None, title: str, file_filter: str) -> Path | None:
        path, _ = QFileDialog.getOpenFileName(parent, title, "", file_filter)
        return Path(path) if path else None

    def save_file(self, parent: QWidget | None, title: str, suggested: str,
                  file_filter: str) -> Path | None:
        path, _ = QFileDialog.getSaveFileName(parent, title, suggested, file_filter)
        return Path(path) if path else None

    def choose_directory(self, parent: QWidget | None, title: str, start: Path) -> Path | None:
        path = QFileDialog.getExistingDirectory(parent, title, str(start))
        return Path(path) if path else None

    def confirm_dry_run(self, parent: QWidget | None, script: str,
                        issues: Sequence[Issue]) -> bool:
        return DryRunDialog(parent, script, issues).exec() == QDialog.DialogCode.Accepted

    def ask_reset_identity(self, parent: QWidget | None, identity: str, *,
                           keep_users: bool = False) -> str | None:
        dialog = ResetIdentityDialog(parent, identity, keep_users=keep_users)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.input.text()
