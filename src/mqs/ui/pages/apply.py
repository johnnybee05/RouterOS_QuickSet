"""Aplikovat: mode, safety options, preflight, dry-run, progress, checklist, confirm."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mqs.apply.orchestrator import (
    ApplyAborted,
    ApplyContext,
    ApplyEvent,
    ApplyOrchestrator,
    ApplyReport,
)
from mqs.apply.plan import ApplyMode, ApplyOptions, build_upload_script, make_names
from mqs.apply.preflight import collect_existing, preflight
from mqs.apply.reconnect import reconnect_with_retry
from mqs.apply.session import DeviceSession, build_apply_context, build_uploader_factory
from mqs.apply.verify import CheckResult
from mqs.issues import Issue, Severity
from mqs.messages_cs import STEP_PREFIX
from mqs.paths import backups_dir
from mqs.transport.base import Transport
from mqs.ui import strings_cs as S
from mqs.ui.controller import Analysis, ProfileController
from mqs.ui.dialogs import Dialogs
from mqs.ui.pages.base import Page
from mqs.ui.theme import ERROR_RED, OK_GREEN
from mqs.ui.widgets.common import IssueList, LogView
from mqs.ui.workers import TaskRunner

log = logging.getLogger(__name__)
IDENTITY_CHECKED_IN_DIALOG = "apply.reset_identity_mismatch"


class ApplyPage(Page):
    sessionReplaced = Signal(object)

    def __init__(
        self,
        controller: ProfileController,
        runner: TaskRunner,
        dialogs: Dialogs,
        session_provider: Callable[[], DeviceSession | None],
        parent: QWidget | None = None,
        *,
        save_script: Callable[[], None] = lambda: None,
        orchestrator_factory: Callable[[ApplyContext], Any] = ApplyOrchestrator,
        collect_existing_fn: Callable[[Transport], Any] = collect_existing,
        uploader_factory_builder: Callable[..., Any] = build_uploader_factory,
        reconnect_fn: Callable[..., Transport] = reconnect_with_retry,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        super().__init__(controller, parent)
        self.runner, self.dialogs, self.session_provider = runner, dialogs, session_provider
        self.save_script = save_script
        self.orchestrator_factory = orchestrator_factory
        self.collect_existing_fn = collect_existing_fn
        self.uploader_factory_builder = uploader_factory_builder
        self.reconnect_fn = reconnect_fn
        self.now = now
        self.orchestrator: Any = None
        self.last_report: ApplyReport | None = None

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(self._mode_group(), 1)
        top.addWidget(self._options_group(), 1)
        layout.addLayout(top)

        buttons = QHBoxLayout()
        self.apply_button = QPushButton(S.APPLY_DRY_RUN)
        self.apply_button.clicked.connect(self.start_apply)
        self.confirm_button = QPushButton(S.APPLY_CONFIRM_WORKS)
        self.confirm_button.clicked.connect(self.confirm_works)
        self.confirm_button.hide()
        self.result_label = QLabel()
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.confirm_button)
        buttons.addWidget(self.result_label, 1)
        layout.addLayout(buttons)

        splitter = QSplitter(Qt.Orientation.Vertical)
        preflight_box = QGroupBox(S.APPLY_PREFLIGHT)
        self.preflight_list = IssueList()
        QVBoxLayout(preflight_box).addWidget(self.preflight_list)
        splitter.addWidget(preflight_box)
        result_split = QSplitter(Qt.Orientation.Horizontal)
        checklist_box = QGroupBox(S.APPLY_CHECKLIST)
        self.checklist = QTableWidget(0, 2)
        self.checklist.horizontalHeader().hide()
        self.checklist.verticalHeader().hide()
        self.checklist.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        QVBoxLayout(checklist_box).addWidget(self.checklist)
        log_box = QGroupBox(S.APPLY_LOG)
        self.log_view = LogView()
        QVBoxLayout(log_box).addWidget(self.log_view)
        result_split.addWidget(checklist_box)
        result_split.addWidget(log_box)
        splitter.addWidget(result_split)
        layout.addWidget(splitter, 1)
        self.refresh()

    # ------------------------------------------------------------ layout

    def _mode_group(self) -> QGroupBox:
        box = QGroupBox(S.APPLY_MODE_GROUP)
        layout = QVBoxLayout(box)
        self.mode_group = QButtonGroup(self)
        self.mode_buttons: dict[ApplyMode, QRadioButton] = {}
        for index, (value, label) in enumerate(S.APPLY_MODES.items()):
            button = QRadioButton(label)
            self.mode_group.addButton(button, index)
            self.mode_buttons[ApplyMode(value)] = button
            layout.addWidget(button)
        self.mode_buttons[ApplyMode.IMPORT].setChecked(True)
        self.mode_group.idToggled.connect(lambda _i, on: on and self.refresh())
        self.dialect_confirm = QCheckBox(S.DIALECT_MISMATCH_CONFIRM)
        self.dialect_confirm.toggled.connect(lambda _: self.refresh())
        layout.addWidget(self.dialect_confirm)
        return box

    def _options_group(self) -> QGroupBox:
        box = QGroupBox()
        form = QFormLayout(box)
        self.rollback = QCheckBox(S.APPLY_ROLLBACK)
        self.rollback_minutes = QSpinBox()
        self.rollback_minutes.setRange(1, 120)
        self.rollback_minutes.setValue(5)
        self.rollback.toggled.connect(lambda _: self.refresh())
        self.rollback_minutes.valueChanged.connect(lambda _: self.refresh())
        # Long checkbox texts get their own row, so the inputs keep a usable width.
        form.addRow(self.rollback)
        form.addRow("", self.rollback_minutes)
        self.download = QCheckBox(S.APPLY_DOWNLOAD)
        self.download.setChecked(True)
        self.download_dir = QLineEdit(str(backups_dir()))
        form.addRow(self.download)
        form.addRow("", self.download_dir)
        self.reset_delay = QSpinBox()
        self.reset_delay.setRange(0, 300)
        self.reset_delay.setValue(15)
        form.addRow(S.APPLY_RESET_DELAY, self.reset_delay)
        self.admin_password = QLineEdit()
        self.admin_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.admin_password.textChanged.connect(lambda _: self.refresh())
        form.addRow(S.APPLY_ADMIN_PASSWORD, self.admin_password)
        return box

    # ------------------------------------------------------------ state

    @property
    def mode(self) -> ApplyMode:
        return next(m for m, b in self.mode_buttons.items() if b.isChecked())

    def options(self, confirmed_identity: str = "") -> ApplyOptions:
        mode = self.mode
        return ApplyOptions(
            mode=mode,
            rollback_minutes=self.rollback_minutes.value() if self.rollback.isChecked()
            and mode == ApplyMode.IMPORT else None,
            reset_delay_s=self.reset_delay.value(),
            admin_password=self.admin_password.text(),
            download_dir=Path(self.download_dir.text()) if self.download.isChecked() else None,
            confirm_dialect_mismatch=self.dialect_confirm.isChecked(),
            confirmed_identity=confirmed_identity,
        )

    def on_analysis(self, analysis: Analysis) -> None:
        self.refresh()

    def on_inventory(self, inventory) -> None:
        self.refresh()

    def preflight_issues(self, *, existing: Any = None,
                         confirmed_identity: str = "") -> list[Issue]:
        analysis = self.controller.analysis or self.controller.recompute()
        session = self.session_provider()
        return preflight(
            analysis.profile, analysis.primary, analysis.validation,
            self.options(confirmed_identity),
            session.inventory if session else None,
            existing=existing,
            session_port=session.session_port if session else None,
        )

    def refresh(self) -> None:
        mode = self.mode
        reset = mode == ApplyMode.RESET_AND_IMPORT
        self.reset_delay.setEnabled(reset)
        self.admin_password.setEnabled(reset)
        self.rollback.setEnabled(mode == ApplyMode.IMPORT)
        self.rollback_minutes.setEnabled(mode == ApplyMode.IMPORT)
        self.apply_button.setText(S.APPLY_SAVE_SCRIPT if mode == ApplyMode.GENERATE_ONLY
                                  else S.APPLY_DRY_RUN)
        issues = self.preflight_issues()
        self.preflight_list.set_issues(issues)
        blocking = [i for i in issues
                    if i.severity == Severity.ERROR and i.code != IDENTITY_CHECKED_IN_DIALOG]
        self.apply_button.setEnabled(not blocking and not self.runner.busy)

    # ------------------------------------------------------------ apply flow

    def start_apply(self) -> None:
        if self.mode == ApplyMode.GENERATE_ONLY:
            self.save_script()
            return
        session = self.session_provider()
        if session is None:
            self.dialogs.error(self, S.APPLY_NOT_CONNECTED)
            return
        self.apply_button.setEnabled(False)
        self.runner.start(lambda token, report: self.collect_existing_fn(session.transport),
                          on_finished=lambda existing: self._after_collisions(session, existing),
                          on_failed=self._failed)

    def _after_collisions(self, session: DeviceSession, existing: Any) -> None:
        issues = self.preflight_issues(existing=existing)
        blocking = [i for i in issues
                    if i.severity == Severity.ERROR and i.code != IDENTITY_CHECKED_IN_DIALOG]
        if blocking:
            self.dialogs.error(self, "\n".join(i.message for i in blocking))
            self.refresh()
            return
        result = self.controller.generate_for_apply()
        options = self.options()
        names = make_names(self.now(), session.inventory.has_flash_dir)
        preview = build_upload_script(result.text, options, names, mask_secrets=True)
        if not self.dialogs.confirm_dry_run(self, preview, issues):
            self.refresh()
            return
        if options.mode == ApplyMode.RESET_AND_IMPORT:
            typed = self.dialogs.ask_reset_identity(self, session.inventory.identity)
            if typed is None:
                self.refresh()
                return
            options = self.options(confirmed_identity=typed)
            identity_errors = [
                i for i in preflight(self.controller.profile, result, [], options,
                                     session.inventory)
                if i.code == IDENTITY_CHECKED_IN_DIALOG
            ]
            if identity_errors:
                self.dialogs.error(self, identity_errors[0].message)
                self.refresh()
                return
        self._run(session, result, options)

    def _run(self, session: DeviceSession, result: Any, options: ApplyOptions) -> None:
        self.checklist.setRowCount(0)
        self.confirm_button.hide()
        self.result_label.setText("")

        def task(token, report):
            ctx = build_apply_context(
                self.controller.profile, result, options, session,
                on_event=report, now=self.now, cancelled=token.cancelled,
                uploader_factory_builder=self.uploader_factory_builder,
                reconnect_fn=self.reconnect_fn,
            )
            self.orchestrator = self.orchestrator_factory(ctx)
            return self.orchestrator.run()

        self.runner.start(task, on_finished=self._finished, on_failed=self._failed,
                          on_progress=self._event)

    def _event(self, event: ApplyEvent) -> None:
        self.log_view.append_line(f"[{STEP_PREFIX[event.status]}] {event.message}")
        log.info("%s %s %s", event.status.value, event.step, event.detail)

    def _show_report(self, report: ApplyReport) -> None:
        self.last_report = report
        self.show_checklist(report.checks)
        for issue in report.issues:
            self.log_view.append_line(issue.message)
        self.confirm_button.setVisible(report.rollback_pending)
        session = self.session_provider()
        if session is not None and report.transport is not session.transport:
            self.sessionReplaced.emit(report.transport)

    def _finished(self, report: ApplyReport) -> None:
        self._show_report(report)
        self.result_label.setText(S.APPLY_DONE if report.succeeded else S.APPLY_DONE_WITH_WARNINGS)
        self.refresh()

    def _failed(self, exc: BaseException) -> None:
        if isinstance(exc, ApplyAborted):
            self._show_report(exc.report)
            message = exc.issue.message
        else:
            message = f"{type(exc).__name__}: {exc}"
        self.result_label.setText(message)
        self.dialogs.error(self, message)
        self.refresh()

    def show_checklist(self, checks: list[CheckResult]) -> None:
        self.checklist.setRowCount(len(checks))
        for row, check in enumerate(checks):
            self.checklist.setItem(row, 0, QTableWidgetItem(check.message))
            status = QTableWidgetItem(S.APPLY_CHECK_OK if check.ok else S.APPLY_CHECK_FAIL)
            status.setForeground(QColor(OK_GREEN if check.ok else ERROR_RED))
            self.checklist.setItem(row, 1, status)

    def confirm_works(self) -> None:
        orchestrator = self.orchestrator
        if orchestrator is None:
            return
        self.runner.start(lambda token, report: orchestrator.confirm(),
                          on_finished=lambda _: self.confirm_button.hide(),
                          on_failed=self._failed)

