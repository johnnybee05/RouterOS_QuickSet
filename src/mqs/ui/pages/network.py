"""The *Síť* page: what is out there, and in what order it may be updated.

The window's job here is not to add capability — `mqs scan` and `mqs fleet` already do all
of it — but to make two things impossible to miss on a network of three hundred antennas:
**which devices may be updated at all** (only the ones read off the device itself) and
**what order the batch would go in**. Both are a column and a list rather than something
to work out from a log.

The page owns no logic. Scanning, merging, classifying and ordering all happen in
`mqs/discover` and `mqs/fleet`, which is why they are tested without a window.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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

from mqs.discover.candidates import Candidate, Confidence
from mqs.discover.classify import classify
from mqs.discover.portscan import ScanLimits
from mqs.discover.scan import ScanPlan, ScanResult
from mqs.discover.scan import run as run_scan
from mqs.discover.store import (
    Change,
    Diff,
    Inventory,
    InventoryError,
    compare,
    load,
    save,
    to_csv,
    to_rows,
)
from mqs.discover.topology import describe, update_order
from mqs.paths import app_data_dir
from mqs.transport.mndp import MndpScanner
from mqs.transport.netifs import list_ipv4_interfaces
from mqs.ui import strings_cs as S
from mqs.ui.controller import ProfileController
from mqs.ui.dialogs import Dialogs
from mqs.ui.pages.base import Page
from mqs.ui.workers import CancelToken, TaskRunner

INVENTORY_NAME = "inventory.json"
COLUMNS = ("Stav", "Identity", "IP adresa", "Model", "Kategorie", "Role", "Verze",
           "Architektura", "Wireless", "Zdroje", "Naposledy")


def default_scanner() -> MndpScanner:
    return MndpScanner(list_ipv4_interfaces())


class NetworkPage(Page):
    """Found devices, the inventory they are saved in, and the batch order."""

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
    ) -> None:
        super().__init__(controller, parent)
        self.runner, self.dialogs = runner, dialogs
        self.scanner_factory = scanner_factory
        self.scan_fn = scan_fn
        self.inventory_path = inventory_path or (app_data_dir() / INVENTORY_NAME)
        self.devices: list[Candidate] = []
        self._token: CancelToken | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(self._controls())
        layout.addWidget(self._table_group(), 3)
        layout.addWidget(self._order_group(), 2)
        self._load_saved()
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
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)
        self.legend = QLabel(S.NET_LEGEND)
        self.legend.setWordWrap(True)
        layout.addWidget(self.legend)
        return box

    def _order_group(self) -> QGroupBox:
        box = QGroupBox(S.NET_ORDER_GROUP)
        layout = QVBoxLayout(box)
        self.order_text = QPlainTextEdit()
        self.order_text.setReadOnly(True)
        layout.addWidget(self.order_text)
        self.order_hint = QLabel("")
        self.order_hint.setWordWrap(True)
        layout.addWidget(self.order_hint)
        return box

    # ------------------------------------------------------------ the inventory

    def _load_saved(self) -> None:
        try:
            self.devices = list(load(self.inventory_path).devices)
        except InventoryError as exc:
            self.status.setText(S.NET_INVENTORY_BAD.format(error=exc))
            self.devices = []
        self._show()

    def _save(self) -> None:
        try:
            save(self.inventory_path, Inventory(saved_at=time.time(),
                                                devices=list(self.devices)))
        except OSError as exc:
            self.dialogs.error(self, S.NET_INVENTORY_SAVE_FAILED.format(error=exc))

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

        self._token = self.runner.start(task, on_finished=self._scanned,
                                        on_failed=self._scan_failed)
        self._update_buttons()

    def stop_scan(self) -> None:
        if self._token is not None:
            self._token.cancel()
            self.status.setText(S.NET_SCAN_CANCELLED)

    def _scanned(self, result: ScanResult) -> None:
        self._token = None
        diff = compare(self.devices, list(result.devices))
        self.devices = list(diff.merged)
        self._save()
        self._show(diff)
        failures = "; ".join(f"{r.source.value}: {r.error}" for r in result.failures)
        found = len(result.devices)
        self.status.setText(S.NET_SCAN_DONE.format(found=found, total=len(self.devices))
                            + (f"  {S.NET_SOURCE_FAILED.format(detail=failures)}"
                               if failures else ""))
        self.devicesChanged.emit(list(self.devices))
        self._update_buttons()

    def _scan_failed(self, exc: BaseException) -> None:
        self._token = None
        self.status.setText(S.NET_SCAN_FAILED.format(error=exc))
        self._update_buttons()

    # ------------------------------------------------------------ showing

    def _show(self, diff: Diff | None = None) -> None:
        rows = to_rows(self.devices)
        self.table.setRowCount(len(rows))
        changed = {c.key: c for c in (diff.changes if diff else ())}
        for index, (device, row) in enumerate(zip(self.devices, rows, strict=True)):
            change = changed.get(device.key)
            values = (
                _state(device), row["identity"], row["address"], row["model"],
                row["category"], row["role"], row["version"], row["architecture"],
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
        self._show_order(diff)

    def _show_order(self, diff: Diff | None = None) -> None:
        order = update_order(self.devices)
        lines: list[str] = []
        for number, device in enumerate(order.devices, start=1):
            verdict = classify(device)
            lines.append(f"{number}. {device.identity or device.address or device.key}"
                         f"  ({verdict.summary})")
        for device in order.skipped:
            lines.append(f"-  {device.identity or device.address or device.key}: "
                         f"{S.NET_NOT_CONFIRMED}")
        tree = describe(order.topology, self.devices)
        if tree:
            lines.append("")
            lines.extend(tree)
        self.order_text.setPlainText("\n".join(lines))
        serious = [c for c in (diff.changes if diff else ()) if c.serious]
        if not self.devices:
            hint = S.NET_EMPTY
        elif order.needs_confirmation:
            hint = S.NET_ORDER_UNCERTAIN
        else:
            hint = S.NET_ORDER_OK.format(count=len(order.devices))
        if serious:
            hint = S.NET_SERIOUS.format(detail="; ".join(_describe(c) for c in serious)) \
                + "  " + hint
        self.order_hint.setText(hint)

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
                    Inventory(saved_at=time.time(),
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
        scanning = self._token is not None
        self.scan_button.setEnabled(not scanning)
        self.stop_button.setEnabled(scanning)
        self.export_button.setEnabled(bool(self.devices) and not scanning)

    @property
    def updatable(self) -> list[Candidate]:
        """The devices a batch may touch — the only list anything else should read."""
        return [d for d in self.devices if d.confidence is Confidence.CONFIRMED]

    def shutdown(self) -> None:
        self.stop_scan()


def _state(device: Candidate) -> str:
    return {Confidence.CONFIRMED: S.NET_STATE_CONFIRMED,
            Confidence.PROBABLE: S.NET_STATE_PROBABLE,
            Confidence.UNKNOWN: S.NET_STATE_UNKNOWN}[device.confidence]


def _seen(stamp: float) -> str:
    return time.strftime("%d.%m. %H:%M", time.localtime(stamp)) if stamp else "-"


def _describe(change: Change) -> str:
    return f"{change.name} ({change.detail})" if change.detail else change.name
