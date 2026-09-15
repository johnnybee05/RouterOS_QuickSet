"""Zařízení: network scan (MNDP), connection, detection results, ports, offline target."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mqs.generator.features import is_supported_version
from mqs.logging_setup import SecretRegistry
from mqs.model.inventory import DeviceInventory, Ipv6PackageState
from mqs.transport.base import (
    API_SSL_PORT,
    SSH_PORT,
    AuthError,
    Cancelled,
    ConnectionParams,
    Transport,
    TransportError,
)
from mqs.transport.discovery import collect_inventory, probe_features, session_interface
from mqs.transport.factory import ConnectFailed, TransportChoice, connect_transport
from mqs.transport.mndp import MndpScanner
from mqs.transport.netifs import list_ipv4_interfaces, route_source_address
from mqs.ui import strings_cs as S
from mqs.ui.controller import ProfileController
from mqs.ui.dialogs import Dialogs
from mqs.ui.pages.base import Page, split_list
from mqs.ui.workers import CancelToken, TaskRunner

log = logging.getLogger(__name__)
SCAN_INTERVAL_MS = 1000
SOLICIT_EVERY_TICKS = 10
DEVICE_MAX_AGE_S = 120.0


@dataclass
class DeviceSession:
    params: ConnectionParams
    transport: Transport
    inventory: DeviceInventory
    probes: dict[str, bool] = field(default_factory=dict)
    session_port: str | None = None
    fell_back: bool = False


def describe_error(exc: BaseException) -> str:
    if isinstance(exc, AuthError):
        return S.CONNECT_AUTH_FAILED
    if isinstance(exc, ConnectFailed):
        details = "\n".join(f"• {a.kind.value}:{a.port} – {a.error}" for a in exc.attempts)
        return S.CONNECT_FAILED.format(details=details)
    if isinstance(exc, Cancelled):
        return S.CONNECT_CANCELLED
    if isinstance(exc, TransportError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def find_session_port(transport: Transport, host: str) -> str | None:
    try:
        return session_interface(transport, route_source_address(host))
    except (OSError, TransportError):
        return None


def default_scanner() -> MndpScanner:
    return MndpScanner(list_ipv4_interfaces())


class DevicePage(Page):
    sessionChanged = Signal(object)

    def __init__(
        self,
        controller: ProfileController,
        runner: TaskRunner,
        dialogs: Dialogs,
        registry: SecretRegistry | None = None,
        parent: QWidget | None = None,
        *,
        connect_fn: Callable[..., Any] = connect_transport,
        discover_fn: Callable[..., DeviceInventory] = collect_inventory,
        probe_fn: Callable[..., dict[str, bool]] = probe_features,
        session_port_fn: Callable[[Transport, str], str | None] = find_session_port,
        scanner_factory: Callable[[], Any] = default_scanner,
    ) -> None:
        super().__init__(controller, parent)
        self.runner, self.dialogs, self.registry = runner, dialogs, registry
        self.connect_fn, self.discover_fn, self.probe_fn = connect_fn, discover_fn, probe_fn
        self.session_port_fn = session_port_fn
        self.scanner_factory = scanner_factory
        self.session: DeviceSession | None = None
        self._connect_token: CancelToken | None = None
        self._scanner: Any = None
        self._scan_ticks = 0

        layout = QVBoxLayout(self)
        layout.addWidget(self._scan_group())
        middle = QHBoxLayout()
        middle.addWidget(self._connect_group(), 1)
        middle.addWidget(self._offline_group(), 1)
        layout.addLayout(middle)
        bottom = QHBoxLayout()
        bottom.addWidget(self._info_group(), 1)
        bottom.addWidget(self._ports_group(), 2)
        layout.addLayout(bottom, 1)

        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(SCAN_INTERVAL_MS)
        self._scan_timer.timeout.connect(self.scan_tick)
        self._update_buttons()

    # ------------------------------------------------------------ layout

    def _scan_group(self) -> QGroupBox:
        box = QGroupBox(S.DEVICE_SCAN_GROUP)
        layout = QVBoxLayout(box)
        row = QHBoxLayout()
        self.scan_button = QPushButton(S.DEVICE_SCAN_START)
        self.scan_button.clicked.connect(self.toggle_scan)
        self.mac_button = QPushButton(S.DEVICE_USE_MAC)
        self.mac_button.setEnabled(False)
        self.mac_button.setToolTip(S.DEVICE_MAC_PENDING)
        self.scan_hint = QLabel(S.DEVICE_SCAN_HINT)
        self.scan_hint.setWordWrap(True)
        row.addWidget(self.scan_button)
        row.addWidget(self.mac_button)
        row.addWidget(self.scan_hint, 1)
        layout.addLayout(row)
        self.scan_table = QTableWidget(0, len(S.DEVICE_SCAN_COLUMNS))
        self.scan_table.setHorizontalHeaderLabels(S.DEVICE_SCAN_COLUMNS)
        self.scan_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.scan_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.scan_table.verticalHeader().hide()
        self.scan_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.scan_table.setMaximumHeight(170)
        self.scan_table.cellDoubleClicked.connect(self.use_scanned_device)
        layout.addWidget(self.scan_table)
        return box

    def _connect_group(self) -> QGroupBox:
        box = QGroupBox(S.DEVICE_CONNECT_GROUP)
        form = QFormLayout(box)
        self.host = QLineEdit()
        self.host.setPlaceholderText("192.168.88.1")
        self.user = QLineEdit("admin")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_file = QLineEdit()
        self.transport_choice = QComboBox()
        for value, label in S.TRANSPORTS.items():
            self.transport_choice.addItem(label, TransportChoice(value))
        self.api_port = QSpinBox()
        self.api_port.setRange(1, 65535)
        self.api_port.setValue(API_SSL_PORT)
        self.ssh_port = QSpinBox()
        self.ssh_port.setRange(1, 65535)
        self.ssh_port.setValue(SSH_PORT)
        self.ca_file = QLineEdit()
        form.addRow(S.DEVICE_HOST, self.host)
        form.addRow(S.DEVICE_USER, self.user)
        form.addRow(S.DEVICE_PASSWORD, self.password)
        form.addRow(S.DEVICE_KEY, self.key_file)
        form.addRow(S.DEVICE_TRANSPORT, self.transport_choice)
        form.addRow(S.DEVICE_API_PORT, self.api_port)
        form.addRow(S.DEVICE_SSH_PORT, self.ssh_port)
        form.addRow(S.DEVICE_CA, self.ca_file)
        buttons = QHBoxLayout()
        self.connect_button = QPushButton(S.DEVICE_CONNECT)
        self.connect_button.clicked.connect(self.connect_device)
        self.cancel_button = QPushButton(S.DEVICE_CANCEL)
        self.cancel_button.clicked.connect(self.cancel_connect)
        self.disconnect_button = QPushButton(S.DEVICE_DISCONNECT)
        self.disconnect_button.clicked.connect(self.disconnect_device)
        for button in (self.connect_button, self.cancel_button, self.disconnect_button):
            buttons.addWidget(button)
        form.addRow(buttons)
        self.connect_status = QLabel()
        self.connect_status.setWordWrap(True)
        form.addRow(self.connect_status)
        return box

    def _offline_group(self) -> QGroupBox:
        box = QGroupBox(S.DEVICE_OFFLINE_GROUP)
        form = QFormLayout(box)
        self.offline_version = QComboBox()
        self.offline_version.setEditable(True)
        self.offline_version.addItems(["7.15.3", "6.49.10", "6.40.9"])
        self.offline_version.setCurrentText(str(self.controller.offline_version))
        self.offline_version.currentTextChanged.connect(self._offline_changed)
        self.offline_ports = QLineEdit(",".join(self.controller.offline_ports))
        self.offline_ports.editingFinished.connect(self._offline_changed)
        self.offline_ipv6 = QComboBox()
        for value, label in S.IPV6_STATES.items():
            self.offline_ipv6.addItem(label, Ipv6PackageState(value))
        self.offline_ipv6.currentIndexChanged.connect(self._offline_changed)
        form.addRow(S.DEVICE_OFFLINE_VERSION, self.offline_version)
        form.addRow(S.DEVICE_OFFLINE_PORTS, self.offline_ports)
        form.addRow(S.DEVICE_OFFLINE_IPV6, self.offline_ipv6)
        self.offline_box = box
        return box

    def _info_group(self) -> QGroupBox:
        box = QGroupBox(S.DEVICE_INFO_GROUP)
        form = QFormLayout(box)
        self.info_labels: dict[str, QLabel] = {}
        for key, label in S.INFO_FIELDS.items():
            value = QLabel("—")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.info_labels[key] = value
            form.addRow(label, value)
        return box

    def _ports_group(self) -> QGroupBox:
        box = QGroupBox(S.DEVICE_PORTS_GROUP)
        layout = QVBoxLayout(box)
        self.ports_table = QTableWidget(0, len(S.DEVICE_PORT_COLUMNS))
        self.ports_table.setHorizontalHeaderLabels(S.DEVICE_PORT_COLUMNS)
        self.ports_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ports_table.verticalHeader().hide()
        self.ports_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.ports_table)
        return box

    # ------------------------------------------------------------ offline target

    def _offline_changed(self, *_args: object) -> None:
        if self._loading or self.session is not None:
            return
        try:
            self.controller.set_offline_target(
                version=self.offline_version.currentText().strip(),
                ports=split_list(self.offline_ports.text()),
                ipv6=self.offline_ipv6.currentData(),
            )
        except ValueError:
            return  # incomplete version while typing

    # ------------------------------------------------------------ scanning

    def toggle_scan(self) -> None:
        if self._scanner is not None:
            self.stop_scan()
            return
        self._scanner = self.scanner_factory()
        self._scanner.open()
        if self._scanner.errors:
            self.scan_hint.setText(S.SCAN_ERRORS.format(errors="; ".join(self._scanner.errors)))
        self._scanner.solicit()
        self._scan_ticks = 0
        self._scan_timer.start()
        self.scan_button.setText(S.DEVICE_SCAN_STOP)

    def stop_scan(self) -> None:
        self._scan_timer.stop()
        if self._scanner is not None:
            self._scanner.close()
            self._scanner = None
        self.scan_button.setText(S.DEVICE_SCAN_START)

    def scan_tick(self) -> None:
        if self._scanner is None:
            return
        self._scan_ticks += 1
        if self._scan_ticks % SOLICIT_EVERY_TICKS == 0:
            self._scanner.solicit()
        self._scanner.poll(0)
        devices = self._scanner.devices(max_age=DEVICE_MAX_AGE_S)
        self.scan_table.setRowCount(len(devices))
        for row, device in enumerate(devices):
            cells = [device.identity, str(device.ipv4 or ""), device.mac, device.version,
                     device.board, device.local_interface, device.interface_name]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, device)
                self.scan_table.setItem(row, column, item)

    def use_scanned_device(self, row: int, _column: int = 0) -> None:
        item = self.scan_table.item(row, 0)
        device = item.data(Qt.ItemDataRole.UserRole) if item else None
        if device is not None and device.ipv4 is not None:
            self.host.setText(str(device.ipv4))

    # ------------------------------------------------------------ connection

    def connection_params(self) -> ConnectionParams:
        return ConnectionParams(
            host=self.host.text().strip(),
            username=self.user.text().strip(),
            password=self.password.text(),
            key_filename=self.key_file.text().strip() or None,
            ca_file=self.ca_file.text().strip() or None,
        )

    def connect_device(self) -> None:
        params = self.connection_params()
        if not params.host:
            return
        if self.registry is not None:
            self.registry.add(params.password)
        choice = self.transport_choice.currentData()
        api_port, ssh_port = self.api_port.value(), self.ssh_port.value()
        self.connect_status.setText(S.CONNECT_PROGRESS)

        def task(token: CancelToken, report: Callable[[object], None]) -> DeviceSession:
            outcome = self.connect_fn(params, choice, api_port=api_port, ssh_port=ssh_port,
                                      cancelled=token.cancelled)
            transport = outcome.transport
            token.on_cancel(transport.close)
            inventory = self.discover_fn(transport, on_step=report)
            probes = self.probe_fn(transport, inventory.version)
            port = self.session_port_fn(transport, params.host)
            return DeviceSession(params, transport, inventory, probes, port, outcome.fell_back)

        self._connect_token = self.runner.start(
            task, on_finished=self._connected, on_failed=self._connect_failed,
            on_progress=lambda step: self.connect_status.setText(f"{S.CONNECT_PROGRESS} {step}"))
        self._update_buttons()

    def cancel_connect(self) -> None:
        if self._connect_token is not None:
            self._connect_token.cancel()

    def _connected(self, session: DeviceSession) -> None:
        self._connect_token = None
        self.session = session
        version = session.inventory.version
        status = [f"{S.CONNECTED}: {session.params.host} ({session.transport.kind.value})"]
        if session.fell_back:
            status.append(S.CONNECT_FELL_BACK)
        if not is_supported_version(version):
            status.append(S.CONNECT_UNSUPPORTED.format(version=version))
        self.connect_status.setText("\n".join(status))
        log.info("connected to %s via %s", session.params.host, session.transport.kind.value)
        self.controller.set_inventory(session.inventory, session.probes)
        self.sessionChanged.emit(session)
        self._update_buttons()

    def _connect_failed(self, exc: BaseException) -> None:
        self._connect_token = None
        message = describe_error(exc)
        self.connect_status.setText(message)
        log.warning("connect failed: %s", exc)
        if not isinstance(exc, Cancelled):
            self.dialogs.error(self, message)
        self._update_buttons()

    def disconnect_device(self) -> None:
        if self.session is not None:
            with contextlib.suppress(OSError, TransportError):
                self.session.transport.close()
        self.session = None
        self.connect_status.setText(S.DISCONNECTED)
        self.controller.set_inventory(None)
        self.sessionChanged.emit(None)
        self._update_buttons()

    def replace_transport(self, transport: Transport) -> None:
        """After apply the session may run over a new connection (new SSH port, reboot)."""
        if self.session is not None and transport is not self.session.transport:
            self.session.transport = transport
            self.sessionChanged.emit(self.session)

    def _update_buttons(self) -> None:
        connecting = self._connect_token is not None
        self.connect_button.setEnabled(not connecting and self.session is None)
        self.cancel_button.setEnabled(connecting)
        self.disconnect_button.setEnabled(self.session is not None)
        self.offline_box.setEnabled(self.session is None)

    # ------------------------------------------------------------ inventory display

    def on_inventory(self, inventory: DeviceInventory | None) -> None:
        labels = self.info_labels
        if inventory is None:
            for label in labels.values():
                label.setText("—")
            self.ports_table.setRowCount(0)
            return
        resource = inventory.resource
        rb = inventory.routerboard
        labels["version"].setText(f"{resource.version} {resource.version.channel}".strip())
        labels["board"].setText(resource.board_name)
        labels["architecture"].setText(resource.architecture)
        labels["uptime"].setText(resource.uptime)
        labels["memory"].setText(f"{resource.free_memory // 2**20} / "
                                 f"{resource.total_memory // 2**20} MiB")
        labels["identity"].setText(inventory.identity)
        labels["routerboard"].setText(f"{rb.model} / {rb.current_firmware}" if rb else "—")
        labels["wireless"].setText(inventory.wireless.value)
        labels["ipv6"].setText(S.IPV6_STATES.get(inventory.ipv6_package_state.value,
                                                 inventory.ipv6_package_state.value))
        selectable = set(inventory.port_names)
        rows = [i for i in inventory.interfaces if i.name in selectable]
        self.ports_table.setRowCount(len(rows))
        for row, iface in enumerate(rows):
            chip = inventory.switch_type(iface.switch)
            state = "disabled" if iface.disabled else ("running" if iface.running else "down")
            cells = [iface.name, iface.type, f"{iface.switch} ({chip})" if iface.switch else "—",
                     state, iface.mac_address]
            for column, text in enumerate(cells):
                self.ports_table.setItem(row, column, QTableWidgetItem(text))

    def shutdown(self) -> None:
        self.stop_scan()
        self.cancel_connect()
