"""Aplikovat: mode, safety options, preflight, dry-run, progress, checklist, confirm."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
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
from mqs.apply.plan import (
    DEFAULT_KEEP_BACKUPS,
    DEFAULT_PASSWORD,
    ApplyMode,
    ApplyOptions,
    build_upload_script,
    make_names,
)
from mqs.apply.preflight import collect_existing, preflight, prepare_import
from mqs.apply.reconnect import reconnect_with_retry
from mqs.apply.session import (
    DeviceSession,
    build_apply_context,
    build_uploader_factory,
    build_version_context,
)
from mqs.apply.update import UpdatePlan, plan_from_dir
from mqs.apply.verify import CheckResult
from mqs.apply.version_change import (
    VersionChangeAborted,
    VersionChanger,
    VersionOffer,
    VersionPlan,
    VersionReport,
    offers_from_dir,
)
from mqs.apply.version_change import (
    plan_from_dir as plan_version_from_dir,
)
from mqs.issues import Issue, Severity
from mqs.messages_cs import STEP_PREFIX
from mqs.model.inventory import DeviceInventory
from mqs.model.version import RosVersion
from mqs.paths import backups_dir
from mqs.paths import update_dir as default_update_dir
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
    applied = Signal(object)  # ApplyReport, also of an aborted apply
    # VersionReport: the device runs another RouterOS now, so it is read again.
    version_changed = Signal(object)
    # Nothing is left to do on the device: applied cleanly, or the rollback confirmed.
    finished_with_device = Signal()

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
        update_dir: Path | None = None,
        plan_update_fn: Callable[[Path, DeviceInventory], UpdatePlan] = plan_from_dir,
        offers_fn: Callable[[Path, DeviceInventory], list[VersionOffer]] = offers_from_dir,
        plan_version_fn: Callable[..., VersionPlan] = plan_version_from_dir,
        changer_factory: Callable[..., Any] = VersionChanger,
    ) -> None:
        super().__init__(controller, parent)
        self.runner, self.dialogs, self.session_provider = runner, dialogs, session_provider
        self.save_script = save_script
        self.update_dir = update_dir if update_dir is not None else default_update_dir()
        self.plan_update_fn = plan_update_fn
        self.offers_fn = offers_fn
        self.plan_version_fn = plan_version_fn
        self.changer_factory = changer_factory
        self.offers: list[VersionOffer] = []
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
        self.mode_buttons[ApplyMode.RESET_AND_IMPORT].setChecked(True)
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
        self.keep_backups = QSpinBox()
        self.keep_backups.setRange(0, 99)
        self.keep_backups.setValue(DEFAULT_KEEP_BACKUPS)
        self.keep_backups.setSpecialValueText(S.APPLY_KEEP_BACKUPS_ALL)  # shown for 0
        form.addRow(S.APPLY_KEEP_BACKUPS, self.keep_backups)
        self.reset_delay = QSpinBox()
        self.reset_delay.setRange(0, 300)
        self.reset_delay.setValue(15)
        form.addRow(S.APPLY_RESET_DELAY, self.reset_delay)
        self.keep_users = QCheckBox(S.APPLY_KEEP_USERS)
        self.keep_users.setToolTip(S.APPLY_KEEP_USERS_TIP)
        self.keep_users.toggled.connect(lambda _: self.refresh())
        form.addRow(self.keep_users)
        self.admin_password = QLineEdit(DEFAULT_PASSWORD)
        self.admin_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.admin_password.textChanged.connect(lambda _: self.refresh())
        form.addRow(S.APPLY_ADMIN_PASSWORD, self.admin_password)
        self.update_check = QCheckBox(S.APPLY_UPDATE)
        self.update_check.setChecked(True)
        self.update_check.setToolTip(S.APPLY_UPDATE_TIP)
        self.update_check.toggled.connect(self._update_toggled)
        form.addRow(self.update_check)
        self.update_dir_label = QLabel(S.APPLY_UPDATE_DIR.format(dir=self.update_dir))
        self.update_dir_label.setWordWrap(True)
        self.update_dir_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("", self.update_dir_label)
        self.routerboot_check = QCheckBox(S.APPLY_ROUTERBOOT)
        self.routerboot_check.setChecked(True)
        self.routerboot_check.setToolTip(S.APPLY_ROUTERBOOT_TIP)
        self.routerboot_check.toggled.connect(lambda _: self.refresh())
        form.addRow(self.routerboot_check)
        # Changing the version is not an option of the apply: it happens before one, on its
        # own button, and the update above (which rides the reset's reboot) is then off.
        self.version_check = QCheckBox(S.APPLY_VERSION)
        self.version_check.setToolTip(S.APPLY_VERSION_TIP)
        self.version_check.toggled.connect(self._version_toggled)
        form.addRow(self.version_check)
        version_row = QHBoxLayout()
        self.version_combo = QComboBox()
        self.version_combo.currentIndexChanged.connect(lambda _: self.refresh())
        self.version_button = QPushButton(S.APPLY_VERSION_BUTTON)
        self.version_button.clicked.connect(self.start_version_change)
        version_row.addWidget(self.version_combo, 1)
        version_row.addWidget(self.version_button)
        form.addRow("", version_row)
        self.version_note = QLabel()
        self.version_note.setWordWrap(True)
        form.addRow("", self.version_note)
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
            keep_users=self.keep_users.isChecked() and mode == ApplyMode.RESET_AND_IMPORT,
            download_dir=Path(self.download_dir.text()) if self.download.isChecked() else None,
            confirm_dialect_mismatch=self.dialect_confirm.isChecked(),
            confirmed_identity=confirmed_identity,
            keep_backups=self.keep_backups.value(),
            update=self.update_plan(),
            upgrade_routerboot=mode == ApplyMode.RESET_AND_IMPORT
            and self.routerboot_check.isChecked(),
        )

    def update_plan(self) -> UpdatePlan | None:
        """The packages a reset would install, None while the update is off or cannot be
        worked out yet (no device: the package depends on its architecture)."""
        session = self.session_provider()
        if (self.mode != ApplyMode.RESET_AND_IMPORT or not self.update_check.isChecked()
                or session is None):
            return None
        return self.plan_update_fn(self.update_dir, session.inventory)

    def on_analysis(self, analysis: Analysis) -> None:
        self.refresh()

    def on_inventory(self, inventory) -> None:
        self.fill_versions()
        self.refresh()

    # ------------------------------------------------------------ another version

    def _update_toggled(self, on: bool) -> None:
        if on:
            self.version_check.setChecked(False)
        self.refresh()

    def _version_toggled(self, on: bool) -> None:
        if on:
            self.update_check.setChecked(False)  # one or the other, never both
        self.refresh()

    def fill_versions(self) -> None:
        """The versions the Update directory holds for this device, blocked ones included.

        Kept out of `refresh`, which runs on every keystroke: this reads the directory,
        and rebuilding the list would throw away what the user picked in it.
        """
        session = self.session_provider()
        self.offers = [] if session is None else self.offers_fn(self.update_dir,
                                                                session.inventory)
        self.version_combo.clear()
        for index, offer in enumerate(self.offers):
            if offer.current:
                label = S.APPLY_VERSION_CURRENT.format(version=offer.version)
            elif offer.blocked_by is not None:
                label = S.APPLY_VERSION_BLOCKED.format(version=offer.version)
            else:
                label = f"{offer.version}  ({offer.package.file_name})"
            self.version_combo.addItem(label, str(offer.version))
            if not offer.available:
                # Left in the list on purpose, with the reason: hiding it would leave the
                # user wondering why RouterOS 6 is not on offer for a board built with v7.
                self.version_combo.setItemData(index, False, Qt.ItemDataRole.UserRole - 1)
                if offer.blocked_by is not None:
                    self.version_combo.setItemData(index, offer.blocked_by.message,
                                                   Qt.ItemDataRole.ToolTipRole)
        first = next((i for i, o in enumerate(self.offers) if o.available), -1)
        self.version_combo.setCurrentIndex(first)

    @property
    def chosen_version(self) -> str:
        return str(self.version_combo.currentData() or "")

    def version_plan(self) -> VersionPlan | None:
        """The route to the version picked in the combo, None while none is picked."""
        session = self.session_provider()
        if session is None or not self.chosen_version:
            return None
        return self.plan_version_fn(self.update_dir, session.inventory,
                                    RosVersion.parse(self.chosen_version))

    def refresh_version_row(self) -> None:
        on = self.version_check.isChecked()
        session = self.session_provider()
        self.version_combo.setEnabled(on and bool(self.offers))
        self.version_note.setEnabled(on)
        plan = self.version_plan() if on else None
        if session is None:
            self.version_note.setText("")
        elif not self.offers:
            self.version_note.setText(S.APPLY_VERSION_NONE)
        elif plan is None:
            self.version_note.setText("")
        else:
            floor = plan.floor
            note = [i.message for i in plan.issues]
            note.append(S.APPLY_VERSION_FLOOR.format(floor=floor) if floor
                        else S.APPLY_VERSION_FLOOR_UNKNOWN)
            self.version_note.setText(" ".join(note))
        self.version_button.setEnabled(
            on and plan is not None and plan.changes and not plan.blocked
            and not self.runner.busy)

    def start_version_change(self) -> None:
        session = self.session_provider()
        plan = self.version_plan()
        if session is None:
            self.dialogs.error(self, S.APPLY_NOT_CONNECTED)
            return
        if plan is None or not plan.changes or plan.blocked:
            return
        if plan.needs_confirmation:
            typed = self.dialogs.ask_version_identity(
                self, session.inventory.identity, version=str(plan.target),
                current=str(plan.current), reboots=plan.reboots)
            if typed != session.inventory.identity:
                self.refresh()
                return
        elif not self.dialogs.question(self, S.VERSION_TITLE, S.VERSION_TEXT.format(
                identity=session.inventory.identity, version=plan.target,
                current=plan.current, reboots=plan.reboots)):
            self.refresh()
            return
        self.checklist.setRowCount(0)
        self.result_label.setText("")
        self.version_button.setEnabled(False)
        self.apply_button.setEnabled(False)

        def task(token, report):
            ctx = build_version_context(
                plan, session, on_event=report, profile=self.controller.profile,
                cancelled=token.cancelled,
                uploader_factory_builder=self.uploader_factory_builder,
                reconnect_fn=self.reconnect_fn,
            )
            return self.changer_factory(ctx).run()

        self.runner.start(task, on_finished=self._version_finished,
                          on_failed=self._version_failed, on_progress=self._event)

    def _version_finished(self, report: VersionReport) -> None:
        self.show_checklist(report.checks)
        for issue in report.issues:
            self.log_view.append_line(issue.message)
        installed = report.installed[-1] if report.installed else None
        self.result_label.setText(S.APPLY_VERSION_DONE.format(version=installed)
                                  if installed else S.APPLY_DONE_WITH_WARNINGS)
        self.version_check.setChecked(False)
        self.version_changed.emit(report)
        self.refresh()

    def _version_failed(self, exc: BaseException) -> None:
        if isinstance(exc, VersionChangeAborted):
            self.show_checklist(exc.report.checks)
            for issue in exc.report.issues:
                self.log_view.append_line(issue.message)
            # The device may be on another version already, or on the same one: either way
            # the session has moved and the device is read again before anything else.
            self.version_changed.emit(exc.report)
        message = exc.issue.message if isinstance(exc, VersionChangeAborted) else \
            f"{type(exc).__name__}: {exc}"
        self.result_label.setText(S.APPLY_VERSION_FAILED.format(error=message))
        self.dialogs.error(self, message)
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
            by_mac=session is not None and session.reached_by_mac is not None,
        )

    def refresh(self) -> None:
        mode = self.mode
        reset = mode == ApplyMode.RESET_AND_IMPORT
        self.reset_delay.setEnabled(reset)
        self.keep_users.setEnabled(reset)
        self.update_check.setEnabled(reset)
        self.update_dir_label.setEnabled(reset and self.update_check.isChecked())
        self.routerboot_check.setEnabled(reset)
        self.version_check.setEnabled(self.session_provider() is not None)
        self.refresh_version_row()
        self.admin_password.setEnabled(reset)
        self.admin_password.setPlaceholderText(
            S.APPLY_ADMIN_PASSWORD_KEEP if reset and self.keep_users.isChecked() else "")
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
        options = self.options()
        result = prepare_import(self.controller.generate_for_apply(options.update_target),
                                options, existing)
        names = make_names(self.now(), session.inventory.has_flash_dir)
        preview = build_upload_script(result.text, options, names, mask_secrets=True)
        if not self.dialogs.confirm_dry_run(self, preview, issues):
            self.refresh()
            return
        if options.mode == ApplyMode.RESET_AND_IMPORT:
            typed = self.dialogs.ask_reset_identity(self, session.inventory.identity,
                                                    keep_users=options.keep_users)
            if typed is None:
                self.refresh()
                return
            # The same packages the dry-run showed, even if the directory changed since.
            options = replace(options, confirmed_identity=typed)
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
        # The parameters say where the reconnect went, which file failed and why; the log
        # had only the step code, so a reconnect could not be told from the log to have
        # come back by MAC or by address. Secrets are masked by the log handler.
        log.info("%s %s %s %s", event.status.value, event.step, event.params or "", event.detail)

    def _show_report(self, report: ApplyReport) -> None:
        self.last_report = report
        self.show_checklist(report.checks)
        for issue in report.issues:
            self.log_view.append_line(issue.message)
        self.confirm_button.setVisible(report.rollback_pending)
        self.applied.emit(report)

    def _finished(self, report: ApplyReport) -> None:
        self._show_report(report)
        # A pending rollback still needs the connection for "it works"; failed checks keep
        # it so the device can be looked at and applied to again.
        done = report.succeeded and not report.rollback_pending
        if done:
            self.result_label.setText(S.APPLY_DONE_DISCONNECTED)
            self.finished_with_device.emit()
        else:
            self.result_label.setText(S.APPLY_DONE if report.succeeded
                                      else S.APPLY_DONE_WITH_WARNINGS)
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
                          on_finished=lambda _: self._confirmed(),
                          on_failed=self._failed)

    def _confirmed(self) -> None:
        self.confirm_button.hide()
        self.result_label.setText(S.APPLY_CONFIRMED_DISCONNECTED)
        self.finished_with_device.emit()

