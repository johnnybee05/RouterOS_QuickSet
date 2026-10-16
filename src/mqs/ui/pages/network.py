"""The *Hromadný update* page: what is out there, logging in to it, and updating it in batches.

The window's job here is not to add capability — `mqs scan` and `mqs fleet` do the same —
but to make three things impossible to miss on a network of three hundred antennas:
**which devices may be updated at all** (only the ones read off the device itself),
**what order the batch would go in**, and **what each device would get**. All three are a
column and a list rather than something to work out from a log.

The page owns no logic. Scanning, logging in, ordering, planning and the update of one
device all happen in `mqs/discover` and `mqs/fleet`, which is why they are tested
without a window.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mqs.apply.packages import UpdateDir, scan_update_dir
from mqs.apply.plan import DEFAULT_KEEP_BACKUPS
from mqs.apply.session import DeviceSession, LoginSettings, end_session
from mqs.apply.version_change import VersionChanger, plan_version
from mqs.discover.candidates import Candidate, Confidence, Sighting, confirmed
from mqs.discover.classify import classify
from mqs.discover.merge import find_match, merge_candidates
from mqs.discover.portscan import ScanLimits
from mqs.discover.scan import ScanPlan, ScanResult, log_in, with_neighbours
from mqs.discover.scan import run as run_scan
from mqs.discover.sources import seen_from
from mqs.discover.store import (
    Change,
    Diff,
    Inventory,
    compare,
    save,
    to_csv,
    to_rows,
)
from mqs.discover.topology import Topology, build, update_order
from mqs.fleet.batch import (
    MAX_PARALLEL,
    BatchPlan,
    BatchReport,
    BatchRunner,
    DeviceStep,
    Outcome,
    plan_batch,
)
from mqs.fleet.credentials import CREDENTIALS_NAME, CredentialStore, Remembered
from mqs.fleet.device import update_device
from mqs.issues import Issue, Severity
from mqs.messages_cs import STEP_PREFIX
from mqs.model.inventory import DeviceInventory, WirelessRole
from mqs.model.version import RosVersion
from mqs.paths import app_data_dir, backups_dir
from mqs.paths import update_dir as default_update_dir
from mqs.transport.base import AuthError
from mqs.transport.discovery import collect_inventory
from mqs.transport.mndp import MndpScanner
from mqs.transport.netifs import list_ipv4_interfaces
from mqs.ui import strings_cs as S
from mqs.ui.controller import ProfileController
from mqs.ui.dialogs import Dialogs
from mqs.ui.pages.base import Page
from mqs.ui.pages.device import describe_error
from mqs.ui.widgets.common import STEP_LEVEL, LogLevel, LogView
from mqs.ui.workers import CancelToken, TaskRunner

INVENTORY_NAME = "inventory.json"
COLUMNS = ("Stav", "Identity", "IP adresa", "Model", "Kategorie", "Role", "Verze",
           "Min. verze", "Architektura", "Wireless", "Zdroje", "Naposledy")

OpenFn = Callable[[LoginSettings, str, Callable[[], bool]], DeviceSession]


def default_scanner() -> MndpScanner:
    return MndpScanner(list_ipv4_interfaces())


def default_open(settings: LoginSettings, host: str,
                 cancelled: Callable[[], bool]) -> DeviceSession:
    return settings.open(host, cancelled=cancelled)


def default_read(session: DeviceSession) -> DeviceInventory:
    return collect_inventory(session.transport)


class NetworkPage(Page):
    """Found devices, the inventory they are saved in, and the batch that updates them."""

    devicesChanged = Signal(object)

    def __init__(
        self,
        controller: ProfileController,
        runner: TaskRunner,
        dialogs: Dialogs,
        parent: QWidget | None = None,
        *,
        scanner_factory: Callable[[], Any] = default_scanner,
        scan_fn: Callable[..., ScanResult] = run_scan,
        inventory_path: Path | None = None,
        login_settings: Callable[[], LoginSettings | None] = lambda: None,
        open_fn: OpenFn = default_open,
        close_fn: Callable[[DeviceSession], None] = end_session,
        neighbours_fn: Callable[..., list[Sighting]] = seen_from,
        local_addresses_fn: Callable[[], list[str]] = lambda: [
            str(i.address) for i in list_ipv4_interfaces()],
        read_fn: Callable[[DeviceSession], DeviceInventory] = default_read,
        update_dir: Path | None = None,
        scan_dir_fn: Callable[[Path], UpdateDir] = scan_update_dir,
        changer_factory: Callable[..., Any] = VersionChanger,
        backup_dir: Path | None = None,
        credentials: CredentialStore | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(controller, parent)
        self.runner, self.dialogs = runner, dialogs
        self.scanner_factory = scanner_factory
        self.scan_fn = scan_fn
        self.inventory_path = inventory_path or (app_data_dir() / INVENTORY_NAME)
        self.login_settings = login_settings
        self.open_fn, self.close_fn = open_fn, close_fn
        self.neighbours_fn, self.read_fn = neighbours_fn, read_fn
        self.local_addresses_fn = local_addresses_fn
        self.update_dir = update_dir if update_dir is not None else default_update_dir()
        self.scan_dir_fn = scan_dir_fn
        self.changer_factory = changer_factory
        self.backup_dir = backup_dir if backup_dir is not None else backups_dir()
        # Beside the inventory: the devices, and what each of them took to log in to.
        self.credentials = credentials if credentials is not None else CredentialStore(
            self.inventory_path.with_name(CREDENTIALS_NAME))
        self.now = now
        self.devices: list[Candidate] = []
        self.packages = UpdateDir(self.update_dir, False)
        self._token: CancelToken | None = None
        self._job = ""  # "scan", "login" or "batch" while one runs
        self._diff: Diff | None = None
        self._plan: BatchPlan | None = None  # what the list below shows; refreshed with it

        layout = QVBoxLayout(self)
        layout.addWidget(self._controls())
        layout.addWidget(self._table_group(), 3)
        layout.addWidget(self._batch_group(), 3)
        self.runner.busyChanged.connect(lambda _: self._update_buttons())
        self.load_packages()
        # Every start begins with an empty list: what was out there yesterday is not what
        # is out there now, and a batch must only ever see what this session found. The
        # file is still written, for `mqs fleet` to read.
        self._show()
        self._update_buttons()

    # ------------------------------------------------------------ layout

    def _controls(self) -> QGroupBox:
        box = QGroupBox(S.NET_SCAN_GROUP)
        outer = QVBoxLayout(box)
        row = QHBoxLayout()
        self.scan_button = QPushButton(S.NET_SCAN_START)
        self.scan_button.clicked.connect(self.start_scan)
        self.stop_button = QPushButton(S.NET_SCAN_STOP)
        self.stop_button.clicked.connect(self.stop_scan)
        row.addWidget(self.scan_button)
        row.addWidget(self.stop_button)
        row.addWidget(QLabel(S.NET_SECONDS))
        self.seconds = QSpinBox()
        self.seconds.setRange(2, 120)
        self.seconds.setValue(8)
        row.addWidget(self.seconds)
        row.addStretch(1)
        self.export_button = QPushButton(S.NET_EXPORT)
        self.export_button.clicked.connect(self.export)
        row.addWidget(self.export_button)
        outer.addLayout(row)

        second = QHBoxLayout()
        # Everything below is off by default and says why in its tooltip: these reach
        # machines nobody asked about, or need a secret.
        self.scan_ports = QCheckBox(S.NET_SCAN_PORTS)
        self.scan_ports.setToolTip(S.NET_SCAN_PORTS_HINT)
        second.addWidget(self.scan_ports)
        second.addWidget(QLabel(S.NET_SUBNETS))
        self.subnets = QLineEdit()
        self.subnets.setPlaceholderText("192.168.55.0/24, 10.0.1.0/24")
        second.addWidget(self.subnets, 2)
        second.addWidget(QLabel(S.NET_SNMP))
        self.snmp = QLineEdit()
        self.snmp.setPlaceholderText("public")
        self.snmp.setToolTip(S.NET_SNMP_HINT)
        second.addWidget(self.snmp, 1)
        outer.addLayout(second)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        return box

    def _table_group(self) -> QGroupBox:
        box = QGroupBox(S.NET_TABLE_GROUP)
        layout = QVBoxLayout(box)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table)
        self.legend = QLabel(S.NET_LEGEND)
        self.legend.setWordWrap(True)
        layout.addWidget(self.legend)
        return box

    def _batch_group(self) -> QGroupBox:
        box = QGroupBox(S.NET_ORDER_GROUP)
        layout = QVBoxLayout(box)

        login = QHBoxLayout()
        self.login_button = QPushButton(S.NET_LOGIN)
        self.login_button.setToolTip(S.NET_LOGIN_TIP)
        self.login_button.clicked.connect(self.start_login)
        login.addWidget(self.login_button)
        self.login_note = QLabel("")
        self.login_note.setWordWrap(True)
        login.addWidget(self.login_note, 1)
        self.forget_button = QPushButton(S.NET_FORGET)
        self.forget_button.setToolTip(S.NET_FORGET_TIP)
        self.forget_button.clicked.connect(self.forget_credentials)
        login.addWidget(self.forget_button)
        layout.addLayout(login)

        run = QHBoxLayout()
        run.addWidget(QLabel(S.NET_TARGET))
        self.target = QComboBox()
        self.target.setEditable(True)
        self.target.setMinimumContentsLength(10)
        self.target.setToolTip(S.NET_TARGET_TIP.format(dir=self.update_dir))
        self.target.currentTextChanged.connect(lambda _: self._show_plan())
        run.addWidget(self.target)
        run.addWidget(QLabel(S.NET_HERE))
        self.here = QComboBox()
        self.here.setToolTip(S.NET_HERE_TIP)
        self.here.setMinimumContentsLength(14)
        self.here.currentIndexChanged.connect(lambda _: self._show_plan())
        run.addWidget(self.here)
        run.addWidget(QLabel(S.NET_PARALLEL))
        self.parallel = QSpinBox()
        self.parallel.setRange(1, MAX_PARALLEL)
        self.parallel.setValue(1)
        self.parallel.setToolTip(S.NET_PARALLEL_TIP)
        run.addWidget(self.parallel)
        self.routerboot = QCheckBox(S.NET_ROUTERBOOT)
        self.routerboot.setChecked(True)
        self.routerboot.setToolTip(S.NET_ROUTERBOOT_TIP)
        self.routerboot.toggled.connect(lambda _: self._show_plan())
        run.addWidget(self.routerboot)
        self.routerboot_down = QCheckBox(S.NET_ROUTERBOOT_DOWN)
        self.routerboot_down.setToolTip(S.APPLY_ROUTERBOOT_DOWN_TIP)
        self.routerboot_down.toggled.connect(lambda _: self._show_plan())
        run.addWidget(self.routerboot_down)
        run.addStretch(1)
        self.batch_button = QPushButton(S.NET_BATCH_START)
        self.batch_button.clicked.connect(self.start_batch)
        run.addWidget(self.batch_button)
        self.batch_stop_button = QPushButton(S.NET_BATCH_STOP)
        self.batch_stop_button.setToolTip(S.NET_BATCH_STOP_TIP)
        self.batch_stop_button.clicked.connect(self.stop_job)
        run.addWidget(self.batch_stop_button)
        layout.addLayout(run)

        self.order_text = QPlainTextEdit()
        self.order_text.setReadOnly(True)
        layout.addWidget(self.order_text, 2)
        self.order_hint = QLabel("")
        self.order_hint.setWordWrap(True)
        layout.addWidget(self.order_hint)
        self.log_view = LogView()
        layout.addWidget(self.log_view, 1)
        return box

    # ------------------------------------------------------------ the inventory

    def _save(self) -> None:
        try:
            save(self.inventory_path, Inventory(saved_at=self.now(),
                                                devices=list(self.devices)))
        except OSError as exc:
            self.dialogs.error(self, S.NET_INVENTORY_SAVE_FAILED.format(error=exc))

    def _replace_devices(self, devices: list[Candidate], before: list[Candidate]) -> None:
        self.devices = devices
        self._save()
        self._show(compare(before, devices))
        self.devicesChanged.emit(list(self.devices))

    def load_packages(self) -> None:
        """What the Update directory holds, and the versions it offers as a target."""
        self.packages = self.scan_dir_fn(self.update_dir)
        versions = sorted({p.version for p in self.packages.packages if p.is_main},
                          reverse=True)
        current = self.target.currentText()
        self.target.blockSignals(True)
        self.target.clear()
        self.target.addItems([str(v) for v in versions])
        self.target.setCurrentText(current or (str(versions[0]) if versions else ""))
        self.target.blockSignals(False)

    def showEvent(self, event: Any) -> None:
        # Packages are copied into Update while the window is open; look again on the way in.
        if self._token is None:
            self.load_packages()
            self._show_plan()
        self._show_login_note()
        super().showEvent(event)

    # ------------------------------------------------------------ scanning

    def plan(self) -> ScanPlan:
        subnets = tuple(p.strip() for p in self.subnets.text().split(",") if p.strip())
        return ScanPlan(
            mndp=True,
            mndp_seconds=float(self.seconds.value()),
            subnets=subnets,
            scan_ports=self.scan_ports.isChecked() and bool(subnets),
            scan_limits=ScanLimits(),
            snmp_community=self.snmp.text().strip() if subnets else "",
        )

    def start_scan(self) -> None:
        if self._token is not None:
            return
        plan = self.plan()
        if (self.scan_ports.isChecked() or self.snmp.text().strip()) and not plan.subnets:
            self.dialogs.error(self, S.NET_NEEDS_SUBNET)
            return
        self.status.setText(S.NET_SCANNING)

        def task(token: CancelToken, report: Callable[[object], None]) -> ScanResult:
            return self.scan_fn(plan, mndp_factory=self.scanner_factory,
                                on_source=lambda source, state: report((source, state)),
                                cancelled=token.cancelled)

        self._start("scan", task, self._scanned, self._scan_failed)

    def stop_scan(self) -> None:
        if self._token is not None and self._job == "scan":
            self._token.cancel()
            self.status.setText(S.NET_SCAN_CANCELLED)

    def _scanned(self, result: ScanResult) -> None:
        """A new scan replaces the list: whatever it did not hear is dropped.

        A device it heard again keeps what logging in to it read — its state, neighbours
        and registration tables — so the batch does not need a second login after every
        scan. A device heard only through a neighbour table, and not by this scan, goes
        with the rest; logging in to its AP again brings it back.
        """
        self._done()
        before = list(self.devices)
        found = list(result.devices)
        kept = [d for d in before if find_match(found, d) is not None]
        gone = [d for d in before if find_match(found, d) is None]
        self.devices = merge_candidates([*kept, *found])
        self._save()
        # Against an empty list every row would be "new", which says nothing.
        self._show(compare(before, found) if before else None)
        failures = "; ".join(f"{r.source.value}: {r.error}" for r in result.failures)
        text = S.NET_SCAN_DONE.format(found=len(self.devices))
        if gone:
            text += "  " + S.NET_SCAN_GONE.format(count=len(gone),
                                                  names=", ".join(_name(d) for d in gone))
        if failures:
            text += f"  {S.NET_SOURCE_FAILED.format(detail=failures)}"
        self.status.setText(text)
        self.devicesChanged.emit(list(self.devices))

    def _scan_failed(self, exc: BaseException) -> None:
        self._done()
        self.status.setText(S.NET_SCAN_FAILED.format(error=exc))

    # ------------------------------------------------------------ logging in

    def selected_rows(self) -> list[int]:
        return sorted({index.row() for index in self.table.selectionModel().selectedRows()})

    def selected_devices(self) -> list[Candidate]:
        return [self.devices[row] for row in self.selected_rows() if row < len(self.devices)]

    def start_login(self) -> None:
        rows = self.selected_rows()
        if self._token is not None or not rows:
            return
        settings = self.login_settings()
        if settings is None:
            self.dialogs.error(self, S.NET_LOGIN_NONE)
            return
        chosen = [self.devices[row] for row in rows]
        logins = [self.logins_for(settings, device) for device in chosen]
        self.status.setText(S.NET_LOGGING_IN.format(count=len(chosen)))

        def task(token: CancelToken, report: Callable[[object], None]) -> Any:
            seen: list[Sighting] = []
            own = self.local_addresses_fn()
            used: dict[int, LoginSettings] = {}
            # log_in reads the devices one by one, in the order they were chosen.
            todo = iter(enumerate(logins))

            def read(device: Candidate) -> DeviceInventory:
                index, tries = next(todo)
                if not device.address:
                    raise ValueError(S.NET_NO_ADDRESS)
                try:
                    session, used[index] = self._open(tries, device.address, token.cancelled)
                except Exception as exc:
                    raise RuntimeError(describe_error(exc)) from exc
                try:
                    inventory = session.inventory
                    # What it sees is extra: a device that will not list its neighbours
                    # has still been read.
                    with contextlib.suppress(Exception):
                        seen.extend(self.neighbours_fn(
                            session.transport, inventory.version,
                            found_at=inventory.identity, session_port=session.session_port,
                            own_addresses=own))
                finally:
                    with contextlib.suppress(Exception):
                        self.close_fn(session)
                return inventory

            done = log_in(chosen, read, cancelled=token.cancelled, now=self.now,
                          on_device=lambda device, state: report((_name(device), state)))
            return rows, done, seen, used

        self._start("login", task, self._logged_in, self._login_failed,
                    on_progress=self._login_step)

    def _login_step(self, value: object) -> None:
        name, state = value  # type: ignore[misc]
        self.status.setText(S.NET_LOGIN_STEP.format(
            name=name, state=S.NET_LOGIN_STATES.get(state, state)))

    def _logged_in(self, result: Any) -> None:
        self._done()
        rows, done, seen, used = result
        window = self.login_settings()
        for index, login in sorted(used.items()):
            device = done[index]
            if device.confidence is not Confidence.CONFIRMED:
                continue
            params = login.params
            self._remember(device, params.username, params.password)
            if window is None or (params.username, params.password) != (
                    window.params.username, window.params.password):
                self.log_view.append_line(
                    S.NET_LOGIN_REMEMBERED.format(name=_name(device), user=params.username),
                    LogLevel.OK)
        before = list(self.devices)
        devices = list(self.devices)
        for row, device in zip(rows, done, strict=True):
            devices[row] = device
        self._replace_devices(with_neighbours(devices, seen), before)
        ok = sum(1 for d in done if d.confidence is Confidence.CONFIRMED)
        failed = sum(1 for d in done if d.login_error)
        self.status.setText(S.NET_LOGIN_DONE.format(ok=ok, failed=failed))
        for device in done:
            if device.login_error:
                self.log_view.append_line(f"{_name(device)}: {device.login_error}",
                                          LogLevel.ERROR)

    def _login_failed(self, exc: BaseException) -> None:
        self._done()
        self.status.setText(S.NET_LOGIN_FAILED.format(error=exc))

    def _show_login_note(self) -> None:
        settings = self.login_settings()
        if settings is None:
            text = S.NET_LOGIN_NONE
        else:
            params = settings.params
            text = S.NET_LOGIN_WITH.format(
                user=params.username,
                auth=S.NET_LOGIN_KEY if params.key_filename else S.NET_LOGIN_PASSWORD,
                api=settings.api_port or "-", ssh=settings.ssh_port or "-")
        store = self.credentials
        if store.load_error:
            text += "  " + S.NET_REMEMBERED_UNREADABLE.format(error=store.load_error)
        elif len(store):
            text += "  " + S.NET_REMEMBERED.format(count=len(store))
            if not store.persistent:
                text += " " + S.NET_REMEMBERED_MEMORY
        self.login_note.setText(text)
        self._update_buttons()

    # ------------------------------------------------------------ remembered credentials

    def logins_for(self, settings: LoginSettings, device: Candidate) -> list[LoginSettings]:
        """What to log in to ``device`` with, in order: what it took last time, then the
        window's. Two attempts at most, never the same credentials twice."""
        remembered = self.credentials.find(device)
        if remembered is None or (remembered.username, remembered.password) == (
                settings.params.username, settings.params.password):
            return [settings]
        return [_with(settings, remembered), settings]

    def _open(self, tries: list[LoginSettings], host: str,
              cancelled: Callable[[], bool]) -> tuple[DeviceSession, LoginSettings]:
        """The first of ``tries`` the device lets in. Only a refused login moves on to the
        next: a device that does not answer will not answer the second either."""
        for number, settings in enumerate(tries, start=1):
            try:
                return self.open_fn(settings, host, cancelled), settings
            except AuthError:
                if number == len(tries) or cancelled():
                    raise
        raise AssertionError("no credentials to try")

    def _remember(self, device: Candidate, username: str, password: str) -> None:
        try:
            self.credentials.remember(device, username, password)
        except OSError as exc:
            self.log_view.append_line(S.NET_REMEMBER_FAILED.format(error=exc),
                                      LogLevel.WARNING)
        self._show_login_note()

    def forget_credentials(self) -> None:
        store = self.credentials
        if not len(store) and not store.load_error:
            return
        if not self.dialogs.question(self, S.NET_FORGET,
                                     S.NET_FORGET_CONFIRM.format(count=len(store))):
            return
        try:
            store.forget_all()
        except OSError as exc:
            self.dialogs.error(self, S.NET_REMEMBER_FAILED.format(error=exc))
        self._show_login_note()

    # ------------------------------------------------------------ the batch

    def target_version(self) -> RosVersion | None:
        try:
            return RosVersion.parse(self.target.currentText().strip())
        except ValueError:
            return None

    def batch_devices(self) -> list[Candidate]:
        """The selection, or everything when nothing is selected."""
        return self.selected_devices() or list(self.devices)

    def batch_plan(self) -> BatchPlan | None:
        """Every device's route to the target, None while there is no target to plan for."""
        target = self.target_version()
        devices = self.batch_devices()
        if target is None or not devices:
            return None
        # The tree is the whole network's, not the selection's: an AP picked on its own
        # still has its stations behind it, and the batch has to know they are there.
        routerboot = self.routerboot.isChecked()
        return plan_batch(devices, target, lambda device: self.packages,
                          topology=build(self.devices),
                          here=str(self.here.currentData() or ""),
                          routerboot=routerboot,
                          routerboot_downgrade=routerboot and self.routerboot_down.isChecked())

    def start_batch(self) -> None:
        plan = self.batch_plan()
        if self._token is not None or plan is None or not plan.ready or plan.target is None:
            return
        settings = self.login_settings()
        if settings is None:
            self.dialogs.error(self, S.NET_LOGIN_NONE)
            return
        steps = plan.runnable
        text = S.NET_BATCH_CONFIRM.format(
            count=len(steps), target=plan.target,
            names=", ".join(s.name for s in steps),
            reboots=sum(s.reboots for s in steps))
        firmware = [s.name for s in steps if s.firmware_only]
        if firmware:
            text += "\n\n" + S.NET_BATCH_CONFIRM_FIRMWARE.format(names=", ".join(firmware))
        if plan.needs_confirmation:
            typed = self.dialogs.ask_text(
                self, S.NET_BATCH_TITLE,
                f"{text}\n\n{S.NET_BATCH_WHY.format(reasons=_why(plan))}\n\n"
                f"{S.NET_BATCH_TYPE.format(target=plan.target)}")
            if (typed or "").strip() != str(plan.target):
                return
        elif not self.dialogs.question(self, S.NET_BATCH_TITLE, text):
            return

        target = plan.target
        packages = self.packages
        routerboot = self.routerboot.isChecked()
        routerboot_down = routerboot and self.routerboot_down.isChecked()
        parallel = self.parallel.value()
        backup_dir = self.backup_dir
        self.log_view.append_line(S.NET_BATCH_RUNNING)
        self.status.setText(S.NET_BATCH_RUNNING)

        def task(token: CancelToken, report: Callable[[object], None]) -> Any:
            fresh: dict[str, DeviceInventory | None] = {}

            def run_one(step: DeviceStep) -> None:
                # Stopping the batch never interrupts a device half-way through: the
                # token is the runner's, and the device's own work is not cancellable.
                tries = self.logins_for(settings, step.device)
                fresh[step.device.key] = update_device(
                    step,
                    lambda host: self._open(tries, host, lambda: False)[0],
                    on_event=lambda event: report(("event", step.name, event)),
                    close=self.close_fn,
                    replan=lambda inventory: plan_version(inventory, packages, target),
                    read_again=self.read_fn,
                    changer_factory=self.changer_factory,
                    upgrade_routerboot=routerboot,
                    routerboot_downgrade=routerboot_down,
                    download_dir=backup_dir,
                    keep_backups=DEFAULT_KEEP_BACKUPS)

            outcome = BatchRunner(
                plan, run_one=run_one, parallel=parallel, cancelled=token.cancelled,
                on_step=lambda step: report(("step", step.name, step.outcome, step.detail)),
            ).run()
            return outcome, fresh

        self._start("batch", task, self._batch_finished, self._batch_failed,
                    on_progress=self._batch_progress)

    def _batch_progress(self, value: object) -> None:
        kind, name, *rest = value  # type: ignore[misc]
        if kind == "event":
            event = rest[0]
            self.log_view.append_line(
                f"{name}: [{STEP_PREFIX[event.status]}] {event.message}",
                STEP_LEVEL[event.status])
            return
        outcome, detail = rest
        line = {Outcome.RUNNING: (S.NET_BATCH_STEP_RUNNING, LogLevel.NORMAL),
                Outcome.DONE: (S.NET_BATCH_STEP_DONE, LogLevel.OK),
                Outcome.FAILED: (S.NET_BATCH_STEP_FAILED, LogLevel.ERROR)}.get(outcome)
        if line is not None:
            text, level = line
            self.log_view.append_line(text.format(name=name, detail=detail), level)
            if outcome is Outcome.RUNNING:
                self.status.setText(f"{S.NET_BATCH_RUNNING} {name}")

    def _batch_finished(self, result: tuple[BatchReport, dict[str, Any]]) -> None:
        self._done()
        report, fresh = result
        before = list(self.devices)
        devices = list(self.devices)
        stamp = self.now()
        for index, device in enumerate(devices):
            if device.key not in fresh:
                continue
            inventory = fresh[device.key]
            if inventory is None:
                self.log_view.append_line(S.NET_BATCH_NOT_REREAD.format(name=_name(device)),
                                          LogLevel.WARNING)
                continue
            devices[index] = confirmed(device, inventory, found_at=device.address
                                       ).model_copy(update={"last_seen": stamp})
        self._replace_devices(devices, before)
        done = len(report.of(Outcome.DONE))
        if report.stopped_by is not None:
            message = Issue("fleet.stopped", Severity.ERROR, "", {
                "name": report.stopped_by.name, "error": report.stopped_by.detail,
                "rest": len(report.of(Outcome.SKIPPED))}).message
            self.log_view.append_line(message, LogLevel.ERROR)
            self.status.setText(message)
            self.dialogs.error(self, message)
        elif report.of(Outcome.SKIPPED):
            text = S.NET_BATCH_CANCELLED.format(done=done,
                                                rest=len(report.of(Outcome.SKIPPED)))
            self.log_view.append_line(text, LogLevel.WARNING)
            self.status.setText(text)
        else:
            text = S.NET_BATCH_DONE.format(count=done)
            self.log_view.append_line(text, LogLevel.OK)
            self.status.setText(text)

    def _batch_failed(self, exc: BaseException) -> None:
        self._done()
        text = S.NET_BATCH_FAILED.format(error=f"{type(exc).__name__}: {exc}")
        self.log_view.append_line(text, LogLevel.ERROR)
        self.status.setText(text)
        self.dialogs.error(self, text)

    def stop_job(self) -> None:
        if self._token is None:
            return
        self._token.cancel()
        if self._job == "batch":
            self.status.setText(S.NET_BATCH_STOPPING)
            self.log_view.append_line(S.NET_BATCH_STOPPING, LogLevel.WARNING)

    # ------------------------------------------------------------ running

    def _start(self, job: str, task: Callable[..., Any], finished: Callable[[Any], None],
               failed: Callable[[BaseException], None],
               on_progress: Callable[[object], None] = lambda value: None) -> None:
        self._job = job
        self._token = self.runner.start(task, on_finished=finished, on_failed=failed,
                                        on_progress=on_progress)
        self._update_buttons()

    def _done(self) -> None:
        self._token = None
        self._job = ""
        self._update_buttons()

    # ------------------------------------------------------------ showing

    def _show(self, diff: Diff | None = None) -> None:
        self._diff = diff
        rows = to_rows(self.devices)
        self.table.setRowCount(len(rows))
        changed = {c.key: c for c in (diff.changes if diff else ())}
        for index, (device, row) in enumerate(zip(self.devices, rows, strict=True)):
            change = changed.get(device.key)
            values = (
                _state(device), row["identity"], row["address"], row["model"],
                row["category"], row["role"], row["version"], row["min_version"],
                row["architecture"],
                row["wireless_stack"], row["sources"],
                _seen(device.last_seen),
            )
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if change is not None:
                    item.setToolTip(f"{change.kind.value}: {change.detail}")
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                if column == 0 and device.login_error:
                    item.setToolTip(device.login_error)
                if device.conflicts:
                    item.setToolTip("\n".join(device.conflicts))
                self.table.setItem(index, column, item)
        self._fill_here()
        self._show_plan()

    def _fill_here(self) -> None:
        keep = self.here.currentData()
        self.here.blockSignals(True)
        self.here.clear()
        self.here.addItem(S.NET_HERE_UNKNOWN, "")
        for device in self.devices:
            if device.confidence is Confidence.CONFIRMED:
                self.here.addItem(_name(device), device.key)
        index = self.here.findData(keep) if keep else 0
        self.here.setCurrentIndex(max(index, 0))
        self.here.blockSignals(False)

    def _selection_changed(self) -> None:
        self._show_plan()
        self._update_buttons()

    def _show_plan(self) -> None:
        plan = self._plan = self.batch_plan()
        if plan is not None:
            lines = _plan_lines(plan)
            topology = plan.topology
            needs_order = plan.order.needs_confirmation
            count = len(plan.steps)
        else:
            order = update_order(self.batch_devices(), build(self.devices))
            lines = [S.NET_PLAN_LIST.format(number=number, name=_name(device),
                                            summary=classify(device).summary)
                     for number, device in enumerate(order.devices, start=1)]
            lines += [f"-  {_name(d)}: {S.NET_NOT_CONFIRMED}" for d in order.skipped]
            topology = order.topology
            needs_order = order.needs_confirmation
            count = len(order.devices)
        tree = _tree_lines(topology, self.devices)
        if tree:
            lines += ["", S.NET_TREE, *tree]
        self.order_text.setPlainText("\n".join(lines))

        if not self.devices:
            hint = S.NET_EMPTY
        else:
            parts = [S.NET_ORDER_UNCERTAIN if needs_order
                     else S.NET_ORDER_OK.format(count=count)]
            if plan is not None:
                parts.append(_readiness(plan))
            hint = "  ".join(p for p in parts if p)
        serious = [c for c in (self._diff.changes if self._diff else ()) if c.serious]
        if serious:
            hint = S.NET_SERIOUS.format(detail="; ".join(_describe(c) for c in serious)) \
                + "  " + hint
        self.order_hint.setText(hint)
        self._update_buttons()

    # ------------------------------------------------------------ export

    def export(self) -> None:
        path = self.dialogs.save_file(self, S.NET_EXPORT_TITLE, "inventar.csv",
                                      S.NET_EXPORT_FILTER)
        if path is None:
            return
        rows = to_rows(self.devices)
        try:
            if path.suffix.lower() == ".json":
                path.write_text(
                    Inventory(saved_at=self.now(),
                              devices=list(self.devices)).model_dump_json(indent=2),
                    encoding="utf-8")
            else:
                path.write_text(to_csv(rows), encoding="utf-8")
        except OSError as exc:
            self.dialogs.error(self, S.NET_EXPORT_FAILED.format(error=exc))
            return
        self.status.setText(S.NET_EXPORTED.format(count=len(rows), path=path))

    # ------------------------------------------------------------ state

    def _update_buttons(self) -> None:
        if not hasattr(self, "batch_stop_button"):
            return  # still being built
        working = self._token is not None
        self.scan_button.setEnabled(not working)
        self.stop_button.setEnabled(working and self._job == "scan")
        self.export_button.setEnabled(bool(self.devices) and not working)
        self.login_button.setEnabled(not working and bool(self.selected_rows()))
        plan = None if working else self._plan
        # Not while anything else in the window talks to a device either: a batch that
        # reboots the device an apply is half-way through helps nobody.
        self.batch_button.setEnabled(plan is not None and plan.ready
                                     and not self.runner.busy)
        self.batch_stop_button.setEnabled(working and self._job in ("login", "batch"))
        self.forget_button.setEnabled(not working and (
            bool(len(self.credentials)) or bool(self.credentials.load_error)))
        for widget in (self.target, self.here, self.parallel, self.routerboot):
            widget.setEnabled(not working)
        self.routerboot_down.setEnabled(not working and self.routerboot.isChecked())

    @property
    def updatable(self) -> list[Candidate]:
        """The devices a batch may touch — the only list anything else should read."""
        return [d for d in self.devices if d.confidence is Confidence.CONFIRMED]

    def shutdown(self) -> None:
        self.stop_scan()


def _with(settings: LoginSettings, remembered: Remembered) -> LoginSettings:
    """The window's way in — transport, ports, key — as the remembered user."""
    return replace(settings, params=replace(settings.params, username=remembered.username,
                                            password=remembered.password))


def _name(device: Candidate) -> str:
    return device.identity or device.address or device.key


def _state(device: Candidate) -> str:
    return {Confidence.CONFIRMED: S.NET_STATE_CONFIRMED,
            Confidence.PROBABLE: S.NET_STATE_PROBABLE,
            Confidence.UNKNOWN: S.NET_STATE_UNKNOWN}[device.confidence]


def _seen(stamp: float) -> str:
    return time.strftime("%d.%m. %H:%M", time.localtime(stamp)) if stamp else "-"


def _describe(change: Change) -> str:
    return f"{change.name} ({change.detail})" if change.detail else change.name


def _role(device: Candidate) -> str:
    roles = device.radios.roles
    if WirelessRole.AP in roles:
        return S.NET_ROLES["ap"]
    if WirelessRole.STATION in roles:
        return S.NET_ROLES["station"]
    return S.NET_ROLES["none"] if device.inventory else S.NET_ROLES["unknown"]


def _plan_lines(plan: BatchPlan) -> list[str]:
    """The batch as the engineer reads it before allowing it: order, route, files."""
    lines: list[str] = []
    for number, step in enumerate(plan.steps, start=1):
        current = step.plan.current if step.plan is not None else "?"
        head = S.NET_PLAN_DEVICE.format(number=number, name=step.name,
                                        role=_role(step.device), current=current)
        if step.behind:
            head = head[:-1] + S.NET_PLAN_BEHIND.format(count=len(step.behind)) + ")"
        lines.append(head)
        floor = step.plan.floor if step.plan is not None else None
        lines.append(S.NET_PLAN_FLOOR.format(floor=floor) if floor
                     else S.NET_PLAN_FLOOR_UNKNOWN)
        if step.outcome is Outcome.BLOCKED:
            lines.append(S.NET_PLAN_BLOCKED.format(detail=step.detail or "?"))
            continue
        if step.firmware_only and step.firmware is not None:
            line = (S.NET_PLAN_ROUTERBOOT_DOWN if step.firmware.older
                    else S.NET_PLAN_ROUTERBOOT)
            lines.append(line.format(target=plan.target, current=step.firmware.current,
                                     version=step.firmware.available))
            continue
        if step.outcome is Outcome.UNCHANGED or step.plan is None:
            lines.append(S.NET_PLAN_UNCHANGED.format(target=plan.target))
            continue
        for hop in step.plan.hops:
            lines.append(S.NET_PLAN_HOP.format(
                version=hop.version, files=", ".join(p.file_name for p in hop.packages)))
        if step.plan.drivers:
            lines.append(S.NET_PLAN_DRIVERS.format(drivers=", ".join(sorted(step.plan.drivers))))
        if step.plan.needs_confirmation:
            lines.append(S.NET_PLAN_CONFIRM)
    lines += [f"-  {_name(d)}: {S.NET_NOT_CONFIRMED}" for d in plan.order.skipped]
    return lines


def _tree_lines(topology: Topology, devices: list[Candidate]) -> list[str]:
    names = {d.key: _name(d) for d in devices}
    lines = [S.NET_TREE_LINK.format(parent=names.get(link.parent, link.parent),
                                    through=f" ({link.interface})" if link.interface else "",
                                    child=names.get(link.child, link.child))
             for link in sorted(topology.links, key=lambda link: (link.parent, link.child))]
    # Orphans only when there is a tree to be missing from: on a network where nothing is
    # linked, every device saying so is noise, and the hint already says the order is a guess.
    if topology.links:
        lines += [S.NET_TREE_ORPHAN.format(name=names.get(key, key))
                  for key in topology.orphans]
    return lines


def _readiness(plan: BatchPlan) -> str:
    if plan.blocked:
        return S.NET_BATCH_BLOCKED.format(names=", ".join(s.name for s in plan.blocked))
    if not plan.runnable:
        return S.NET_BATCH_NOTHING.format(target=plan.target) if plan.steps else ""
    return S.NET_BATCH_READY.format(
        count=len(plan.runnable),
        reboots=sum(s.reboots for s in plan.runnable))


def _why(plan: BatchPlan) -> str:
    reasons = []
    if plan.order.needs_confirmation:
        reasons.append(S.NET_BATCH_WHY_ORDER)
    crossing = [s.name for s in plan.runnable
                if s.plan is not None and s.plan.needs_confirmation]
    if crossing:
        reasons.append(S.NET_BATCH_WHY_VERSION.format(names=", ".join(crossing)))
    return "; ".join(reasons)
