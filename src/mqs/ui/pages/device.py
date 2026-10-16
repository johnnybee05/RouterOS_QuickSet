"""Zařízení: network scan (MNDP), connection, detection results, ports, offline target."""

from __future__ import annotations

import logging
from collections.abc import Callable
from ipaddress import IPv4Address
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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

from mqs.apply.orchestrator import ApplyReport
from mqs.apply.session import (
    DeviceSession,
    LoginSettings,
    discover_session,
    end_session,
    find_session_port,
    open_mac_session,
    session_after_apply,
    session_after_version_change,
)
from mqs.capabilities.chips import device_notes
from mqs.generator.features import is_supported_version
from mqs.logging_setup import SecretRegistry
from mqs.messages import device_note
from mqs.model.inventory import DeviceInventory, Ipv6PackageState
from mqs.transport.base import (
    API_SSL_PORT,
    SSH_PORT,
    AuthError,
    Cancelled,
    ConnectionParams,
    KeyPassphraseError,
    Transport,
    TransportError,
)
from mqs.transport.discovery import collect_inventory, probe_features
from mqs.transport.factory import ConnectFailed, TransportChoice, connect_transport
from mqs.transport.mac_connect import (
    MacConnectResult,
    bootstrap_interface,
    connect_by_mac,
    local_interface_for,
)
from mqs.transport.mndp import DiscoveredDevice, MndpScanner
from mqs.transport.netifs import (
    LocalInterface,
    list_ipv4_interfaces,
    propose_free_address,
)
from mqs.ui import strings as S
from mqs.ui.controller import ProfileController
from mqs.ui.dialogs import Dialogs
from mqs.ui.pages.base import Page, mark_secret, split_list
from mqs.ui.settings import SavedLogin
from mqs.ui.workers import CancelToken, TaskRunner

log = logging.getLogger(__name__)
SCAN_INTERVAL_MS = 1000
SOLICIT_EVERY_TICKS = 10
DEVICE_MAX_AGE_S = 120.0


def describe_error(exc: BaseException) -> str:
    if isinstance(exc, KeyPassphraseError):
        return S.CONNECT_KEY_PASSPHRASE_WRONG if exc.wrong else S.CONNECT_KEY_PASSPHRASE
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


def connection_summary(session: DeviceSession, *, with_port: bool = False) -> str:
    """"Připojeno: …" naming the device the way the user reached it.

    A device reached by MAC is named by its MAC: the address the transport runs over is
    only lent to it, and after an apply the session comes back by MAC again.
    """
    transport = session.transport
    kind = f"{transport.kind.value}:{transport.port}" if with_port else transport.kind.value
    route = session.reached_by_mac
    if route is not None:
        return S.CONNECTED_BY_MAC.format(mac=route.mac, method=session.mac_method,
                                         transport=kind, host=session.params.host)
    return f"{S.CONNECTED}: {session.params.host} ({kind})"


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
        mac_connect_fn: Callable[..., MacConnectResult] = connect_by_mac,
        local_interfaces_fn: Callable[[], list[LocalInterface]] = list_ipv4_interfaces,
        propose_fn: Callable[..., IPv4Address | None] = propose_free_address,
    ) -> None:
        super().__init__(controller, parent)
        self.runner, self.dialogs, self.registry = runner, dialogs, registry
        self.connect_fn, self.discover_fn, self.probe_fn = connect_fn, discover_fn, probe_fn
        self.session_port_fn = session_port_fn
        self.scanner_factory = scanner_factory
        self.mac_connect_fn = mac_connect_fn
        self.local_interfaces_fn = local_interfaces_fn
        self.propose_fn = propose_fn
        self.session: DeviceSession | None = None
        self._connect_token: CancelToken | None = None
        self._returning = False  # giving a lent address back after a disconnect
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
        self.mac_button.setToolTip(S.DEVICE_MAC_HINT)
        self.mac_button.clicked.connect(self.connect_by_mac_clicked)
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
        self.scan_table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.scan_table)
        return box

    def _connect_group(self) -> QGroupBox:
        box = QGroupBox(S.DEVICE_CONNECT_GROUP)
        form = QFormLayout(box)
        self.host = QLineEdit()
        self.host.setPlaceholderText("192.168.88.1")
        self.user = QLineEdit("admin")
        self.password = mark_secret(QLineEdit())
        self.key_file = QLineEdit()
        self.key_passphrase = mark_secret(QLineEdit())
        self.key_passphrase.setPlaceholderText(S.DEVICE_KEY_PASSPHRASE_HINT)
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
        form.addRow(S.DEVICE_KEY_PASSPHRASE, self.key_passphrase)
        form.addRow(S.DEVICE_TRANSPORT, self.transport_choice)
        form.addRow(S.DEVICE_API_PORT, self.api_port)
        form.addRow(S.DEVICE_SSH_PORT, self.ssh_port)
        form.addRow(S.DEVICE_CA, self.ca_file)
        self.remember_login = QCheckBox(S.DEVICE_REMEMBER_LOGIN)
        self.remember_login.setToolTip(S.DEVICE_REMEMBER_LOGIN_TIP)
        form.addRow(self.remember_login)
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
        self.device_notes = QLabel()
        self.device_notes.setWordWrap(True)
        self.device_notes.hide()
        form.addRow(self.device_notes)
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
                ipv6=Ipv6PackageState(self.offline_ipv6.currentData()),
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
            key_passphrase=self.key_passphrase.text(),
            ca_file=self.ca_file.text().strip() or None,
        )

    def login_settings(self) -> LoginSettings | None:
        """The credentials in the form, for logging in elsewhere (the *Hromadný update* page).

        None without a user name: there is nothing to log in with. The secrets are
        registered for redaction exactly as a connect from this page registers them.
        """
        params = self.connection_params()
        if not params.username:
            return None
        if self.registry is not None:
            self.registry.add(params.password)
            self.registry.add(params.key_passphrase)
        return LoginSettings(params, TransportChoice(self.transport_choice.currentData()),
                             api_port=self.api_port.value(), ssh_port=self.ssh_port.value())

    def saved_login(self) -> SavedLogin:
        """The form as it is now, to be restored at the next start."""
        return SavedLogin(
            host=self.host.text().strip(), username=self.user.text().strip(),
            password=self.password.text(), key_file=self.key_file.text().strip(),
            key_passphrase=self.key_passphrase.text(),
            transport=TransportChoice(self.transport_choice.currentData()).value,
            api_port=self.api_port.value(), ssh_port=self.ssh_port.value(),
            ca_file=self.ca_file.text().strip())

    def restore_login(self, login: SavedLogin) -> None:
        """Fill the form from the last run. A port of 0 (never saved) or a transport
        this version does not know leaves that field at the page's own default."""
        self.host.setText(login.host)
        self.user.setText(login.username)
        self.password.setText(login.password)
        self.key_file.setText(login.key_file)
        self.key_passphrase.setText(login.key_passphrase)
        self.ca_file.setText(login.ca_file)
        for value, box in ((login.api_port, self.api_port), (login.ssh_port, self.ssh_port)):
            if value:
                box.setValue(value)
        index = next((i for i in range(self.transport_choice.count())
                      if TransportChoice(self.transport_choice.itemData(i)).value
                      == login.transport), -1)
        if index >= 0:
            self.transport_choice.setCurrentIndex(index)

    def _discover_session(
        self,
        transport: Transport,
        params: ConnectionParams,
        report: Callable[[object], None],
        *,
        fell_back: bool = False,
    ) -> DeviceSession:
        return discover_session(
            transport, params, on_step=report, fell_back=fell_back,
            discover=self.discover_fn, probe=self.probe_fn, session_port=self.session_port_fn,
        )

    def connect_device(self) -> None:
        params = self.connection_params()
        if not params.host:
            return
        if self.registry is not None:
            self.registry.add(params.password)
            self.registry.add(params.key_passphrase)
        choice = TransportChoice(self.transport_choice.currentData())
        api_port, ssh_port = self.api_port.value(), self.ssh_port.value()
        self.connect_status.setText(S.CONNECT_PROGRESS)

        def task(token: CancelToken, report: Callable[[object], None]) -> DeviceSession:
            outcome = self.connect_fn(params, choice, api_port=api_port, ssh_port=ssh_port,
                                      cancelled=token.cancelled)
            token.on_cancel(outcome.transport.close)
            try:
                return self._discover_session(outcome.transport, params, report,
                                              fell_back=outcome.fell_back)
            except TransportError as exc:
                # Closed under the task by the cancel: "not connected" is how that ends.
                if token.cancelled():
                    raise Cancelled("connect cancelled") from exc
                raise

        self._connect_token = self.runner.start(
            task, on_finished=self._connected, on_failed=self._connect_failed,
            on_progress=lambda step: self.connect_status.setText(f"{S.CONNECT_PROGRESS} {step}"))
        self._update_buttons()

    def selected_scanned_device(self) -> DiscoveredDevice | None:
        rows = {i.row() for i in self.scan_table.selectedItems()}
        if len(rows) != 1:
            return None
        item = self.scan_table.item(next(iter(rows)), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def connect_by_mac_clicked(self) -> None:
        device = self.selected_scanned_device()
        if device is None:
            return
        interface = self._interface_for_device(device)
        if interface is None:
            self.dialogs.error(self, S.MAC_NO_INTERFACE)
            return
        address = self.propose_fn(interface, [])
        if address is None:
            self.dialogs.error(self, S.MAC_NO_ADDRESS)
            return
        cidr = f"{address}/{interface.network.prefixlen}"
        iface_name = bootstrap_interface(device.interface_name)  # where the address goes
        if not self.dialogs.question(self, S.MAC_CONFIRM_TITLE, S.MAC_CONFIRM_TEXT.format(
                identity=device.identity or device.mac, mac=device.mac, address=cidr,
                interface=iface_name)):
            return

        params = self.connection_params()
        if self.registry is not None:
            self.registry.add(params.password)
            self.registry.add(params.key_passphrase)
        choice = TransportChoice(self.transport_choice.currentData())
        api_port, ssh_port = self.api_port.value(), self.ssh_port.value()
        self.connect_status.setText(S.CONNECT_PROGRESS)

        def task(token: CancelToken, report: Callable[[object], None]) -> DeviceSession:
            # A cancel is taken between steps instead of by closing the connection under
            # the task, as the plain connect does: the address lent to the device can only
            # be given back over that connection. On 2026-09-25 a cancel during the
            # MAC-Telnet login closed it and left the address on the device, and the
            # window reported "api-ssl transport is not connected" instead of a cancel.
            def check(_: object = None) -> None:
                if token.cancelled():
                    raise Cancelled("connect cancelled")

            def step(value: object) -> None:
                check()
                report(value)

            return open_mac_session(
                device, params, address, interface, choice,
                api_port=api_port, ssh_port=ssh_port, on_step=step, on_connected=check,
                mac_connect=self.mac_connect_fn, discover=self.discover_fn,
                probe=self.probe_fn, session_port=self.session_port_fn,
            )

        self._connect_token = self.runner.start(
            task, on_finished=self._connected, on_failed=self._connect_failed,
            on_progress=lambda step: self.connect_status.setText(f"{S.CONNECT_PROGRESS} {step}"))
        self._update_buttons()

    def _interface_for_device(self, device: DiscoveredDevice) -> LocalInterface | None:
        return local_interface_for(device, self.local_interfaces_fn())

    def cancel_connect(self) -> None:
        if self._connect_token is not None:
            self._connect_token.cancel()

    def _connected(self, session: DeviceSession) -> None:
        self._connect_token = None
        self.session = session
        self._show_session(session)
        route = session.reached_by_mac
        if route is not None:
            log.info("connected to %s by %s, %s on %s", route.mac, session.mac_method,
                     session.transport.kind.value, session.params.host)
        else:
            log.info("connected to %s via %s", session.params.host,
                     session.transport.kind.value)
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

    def _show_session(self, session: DeviceSession) -> None:
        version = session.inventory.version
        status = [connection_summary(session)]
        if session.temp_address is not None:
            status.append(S.MAC_TEMP_ADDRESS.format(address=session.temp_address))
        if session.fell_back:
            status.append(S.CONNECT_FELL_BACK)
        if not is_supported_version(version):
            status.append(S.CONNECT_UNSUPPORTED.format(version=version))
        self.connect_status.setText("\n".join(status))

    def disconnect_device(self) -> None:
        session, self.session = self.session, None
        self.controller.set_inventory(None)
        self.sessionChanged.emit(None)
        if session is not None and session.temp_address is not None:
            # Off the UI thread: the removal waits a few seconds for an answer that cannot
            # come, because the address it removes is the one the answer would come from.
            address = session.temp_address
            self._returning = True
            self.connect_status.setText(S.MAC_RETURNING.format(address=address))
            self.runner.start(
                lambda token, report: end_session(session),
                on_finished=lambda _: self._returned(S.MAC_RETURNED.format(address=address)),
                on_failed=lambda exc: self._returned(
                    S.MAC_NOT_RETURNED.format(address=address), exc),
            )
        else:
            if session is not None:
                end_session(session)  # nothing lent: this only closes the connection
            self.connect_status.setText(S.DISCONNECTED)
        self._update_buttons()

    def _returned(self, text: str, exc: BaseException | None = None) -> None:
        self._returning = False
        if exc is not None:
            log.warning("temporary address not given back: %s", exc)
        self.connect_status.setText(text)
        self._update_buttons()

    def adopt_version_change(self, report: object) -> None:
        """After a version change the device is read again, not patched up from a report.

        A device that has changed its major version is a different device: the packages,
        the ipv6 package, the wireless stack, the switch chip's name and the flash layout
        are all its own to tell, and the configuration that follows is generated from what
        it says.
        """
        if self.session is None:
            return
        session_after_version_change(self.session, report)
        session = self.session
        self._show_session(session)
        self.runner.start(
            lambda token, step: self._discover_session(session.transport, session.params, step),
            on_finished=self._connected, on_failed=self._connect_failed)

    def adopt_apply(self, report: ApplyReport) -> None:
        """After apply the session may run over a new connection (new SSH port, reboot),
        and a reset has taken the lent address with it."""
        if self.session is None:
            return
        session_after_apply(self.session, report)
        self._show_session(self.session)
        self.sessionChanged.emit(self.session)

    def _update_buttons(self) -> None:
        connecting = self._connect_token is not None
        idle = not connecting and self.session is None and not self._returning
        self.connect_button.setEnabled(idle)
        self.cancel_button.setEnabled(connecting)
        self.disconnect_button.setEnabled(self.session is not None)
        self.offline_box.setEnabled(self.session is None)
        self.mac_button.setEnabled(idle and self.selected_scanned_device() is not None)

    # ------------------------------------------------------------ inventory display

    def on_inventory(self, inventory: DeviceInventory | None) -> None:
        labels = self.info_labels
        if inventory is None:
            for label in labels.values():
                label.setText("—")
            self.device_notes.hide()
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
        notes = device_notes(rb.model if rb else "", resource.board_name)
        if notes:
            self.device_notes.setText(
                S.DEVICE_NOTES + "\n" + "\n".join(f"• {note}" for note in map(device_note, notes))
            )
        self.device_notes.setVisible(bool(notes))
        selectable = set(inventory.port_names)
        rows = [i for i in inventory.interfaces if i.name in selectable]
        self.ports_table.setRowCount(len(rows))
        for row, iface in enumerate(rows):
            chip = inventory.switch_type(iface.switch)
            state = "disabled" if iface.disabled else ("running" if iface.running else "down")
            cells = [iface.name, iface.default_name or "—", iface.type,
                     f"{iface.switch} ({chip})" if iface.switch else "—", state, iface.mac_address]
            for column, text in enumerate(cells):
                self.ports_table.setItem(row, column, QTableWidgetItem(text))

    def shutdown(self) -> None:
        self.stop_scan()
        self.cancel_connect()
