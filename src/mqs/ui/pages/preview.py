"""Náhled: generated script for v6 / v7 / side by side, copy and save."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from mqs.generator.base import generate
from mqs.ui import strings_cs as S
from mqs.ui.controller import Analysis, ProfileController
from mqs.ui.highlighter import RouterOsHighlighter
from mqs.ui.pages.base import Page
from mqs.ui.widgets.common import IssueList, monospace_font

BOTH = 0


def ask_save_path(parent: QWidget, suggested: str) -> Path | None:
    path, _ = QFileDialog.getSaveFileName(parent, S.PREVIEW_SAVE, suggested, S.PREVIEW_SAVE_FILTER)
    return Path(path) if path else None


class PreviewPage(Page):
    def __init__(self, controller: ProfileController, parent: QWidget | None = None,
                 save_dialog: Callable[[QWidget, str], Path | None] = ask_save_path,
                 dark: bool = False) -> None:
        super().__init__(controller, parent)
        self.save_dialog = save_dialog
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.selector = QButtonGroup(self)
        for major, label in ((6, S.PREVIEW_V6), (7, S.PREVIEW_V7), (BOTH, S.PREVIEW_BOTH)):
            button = QRadioButton(label)
            self.selector.addButton(button, major)
            top.addWidget(button)
        self.selector.idToggled.connect(lambda _id, on: on and self._refresh())
        top.addStretch(1)
        self.status = QLabel()
        top.addWidget(self.status)
        self.copy_button = QPushButton(S.PREVIEW_COPY)
        self.copy_button.clicked.connect(self.copy_script)
        self.save_button = QPushButton(S.PREVIEW_SAVE)
        self.save_button.clicked.connect(self.save_script)
        top.addWidget(self.copy_button)
        top.addWidget(self.save_button)
        layout.addLayout(top)

        splitter = QSplitter(Qt.Orientation.Vertical)
        editors = QSplitter(Qt.Orientation.Horizontal)
        self.views: dict[int, QPlainTextEdit] = {}
        self.highlighters: list[RouterOsHighlighter] = []
        for major in (6, 7):
            view = QPlainTextEdit()
            view.setReadOnly(True)
            view.setFont(monospace_font())
            view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            self.highlighters.append(RouterOsHighlighter(view.document(), dark))
            self.views[major] = view
            editors.addWidget(view)
        splitter.addWidget(editors)
        issues_box = QWidget()
        issues_layout = QVBoxLayout(issues_box)
        issues_layout.setContentsMargins(0, 0, 0, 0)
        issues_layout.addWidget(QLabel(S.PREVIEW_ISSUES))
        self.issues = IssueList()
        issues_layout.addWidget(self.issues)
        splitter.addWidget(issues_box)
        splitter.setStretchFactor(0, 3)
        layout.addWidget(splitter)

        self.selector.button(controller.primary_major).setChecked(True)

    @property
    def selected(self) -> int:
        return self.selector.checkedId()

    def selected_major(self) -> int:
        """Dialect used for copy/save: the selected one, or the device dialect when both."""
        return self.selected if self.selected != BOTH else self.controller.primary_major

    def set_dark(self, dark: bool) -> None:
        for highlighter in self.highlighters:
            highlighter.set_dark(dark)

    def on_inventory(self, inventory) -> None:
        self.selector.button(self.controller.primary_major).setChecked(True)

    def on_analysis(self, analysis: Analysis) -> None:
        for major, view in self.views.items():
            view.setPlainText(analysis.results[major].text)
        self._refresh()

    def _refresh(self) -> None:
        selected = self.selected
        for major, view in self.views.items():
            view.setVisible(selected in (major, BOTH))
        analysis = self.controller.analysis
        if analysis is None:
            return
        result = analysis.results[self.selected_major()]
        self.issues.set_issues([*analysis.validation, *result.issues])
        self.status.setText(f"RouterOS {result.target}")

    def script_text(self, major: int) -> str:
        """Unmasked script (passwords included) for Winbox paste or .rsc file."""
        return generate(self.controller.profile, self.controller.context(major)).text

    def copy_script(self) -> None:
        QGuiApplication.clipboard().setText(self.script_text(self.selected_major()))
        self.status.setText(S.PREVIEW_COPIED)

    def save_script(self) -> None:
        major = self.selected_major()
        identity = self.controller.profile.identity or "mikrotik"
        path = self.save_dialog(self, f"{identity}-v{major}.rsc")
        if path is None:
            return
        path.write_text(self.script_text(major), encoding="utf-8", newline="\n")
