"""Základ: identity, scenario presets, port names, WAN."""

from __future__ import annotations

from pydantic import SecretStr
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mqs.model.ports import rename_port
from mqs.model.presets import apply_preset
from mqs.model.profile import DeviceProfile, Mode
from mqs.model.wan import CETIN_VLAN_ID, DhcpWan, PppoeWan, StaticWan, WanType
from mqs.ui import strings as S
from mqs.ui.controller import ProfileController
from mqs.ui.pages.base import Page, mark_secret, parse_cidr, parse_ip, parse_ip_list


class BasicPage(Page):
    def __init__(self, controller: ProfileController, parent: QWidget | None = None) -> None:
        super().__init__(controller, parent)
        layout = QVBoxLayout(self)

        top = QFormLayout()
        self.identity = QLineEdit()
        self.identity.textEdited.connect(lambda text: self.push_value("identity", text))
        top.addRow(S.BASIC_IDENTITY, self.identity)

        mode_row = QHBoxLayout()
        self.mode = QComboBox()
        for value, label in S.MODES.items():
            self.mode.addItem(label, value)
        self.apply_mode = QPushButton(S.MODE_APPLY)
        self.apply_mode.clicked.connect(self._apply_preset)
        mode_row.addWidget(self.mode, 1)
        mode_row.addWidget(self.apply_mode)
        top.addRow(S.BASIC_MODE, mode_row)
        layout.addLayout(top)
        layout.addWidget(self._names_group())

        wan_box = QGroupBox(S.WAN_GROUP)
        form = QFormLayout(wan_box)
        self.wan_enabled = QCheckBox(S.WAN_ENABLED)
        self.wan_enabled.toggled.connect(lambda on: self.push_value("wan.enabled", on))
        form.addRow(self.wan_enabled)
        self.wan_interface = QComboBox()
        self.wan_interface.currentIndexChanged.connect(self._interface_changed)
        form.addRow(S.WAN_INTERFACE, self.wan_interface)

        self.wan_vlan_enabled = QCheckBox(S.WAN_VLAN)
        self.wan_vlan_enabled.toggled.connect(self._vlan_toggled)
        form.addRow(self.wan_vlan_enabled)
        self.wan_vlan_id = QSpinBox()
        self.wan_vlan_id.setRange(1, 4094)
        self.wan_vlan_id.setValue(CETIN_VLAN_ID)
        self.wan_vlan_id.valueChanged.connect(self._vlan_id_changed)
        form.addRow(S.WAN_VLAN_ID, self.wan_vlan_id)
        self.wan_vlan_name = QLineEdit()
        self.wan_vlan_name.setPlaceholderText(f"vlan{CETIN_VLAN_ID}")
        self.wan_vlan_name.textEdited.connect(
            lambda text: self.push_value("wan.vlan_name", text.strip()))
        form.addRow(S.WAN_VLAN_NAME, self.wan_vlan_name)
        vlan_help = QLabel(S.WAN_VLAN_HELP)
        vlan_help.setWordWrap(True)
        form.addRow(vlan_help)

        self.wan_type = QComboBox()
        for value, label in S.WAN_TYPES.items():
            self.wan_type.addItem(label, value)
        self.wan_type.currentIndexChanged.connect(self._type_changed)
        form.addRow(S.WAN_TYPE, self.wan_type)

        self.settings_stack = QStackedWidget()
        self.settings_stack.addWidget(self._dhcp_panel())
        self.settings_stack.addWidget(self._static_panel())
        self.settings_stack.addWidget(self._pppoe_panel())
        form.addRow(self.settings_stack)

        self.wan_list = QCheckBox(S.WAN_LIST)
        self.wan_list.toggled.connect(lambda on: self.push_value("wan.add_to_wan_list", on))
        self.wan_mss = QCheckBox(S.WAN_MSS)
        self.wan_mss.toggled.connect(lambda on: self.push_value("wan.mss_clamping", on))
        self.wan_nat = QCheckBox(S.WAN_NAT)
        self.wan_nat.toggled.connect(lambda on: self.push_value("wan.nat_masquerade", on))
        for box in (self.wan_list, self.wan_mss, self.wan_nat):
            form.addRow(box)
        layout.addWidget(wan_box)
        layout.addStretch(1)

        self.fields = {
            "identity": self.identity,
            "mode": self.mode,
            "interface_names": self.port_names,
            "wan.interface": self.wan_interface,
            "wan.vlan_id": self.wan_vlan_id,
            "wan.vlan_name": self.wan_vlan_name,
            "wan.settings.address": self.static_address,
            "wan.settings.gateway": self.static_gateway,
            "wan.settings.dns_servers": self.static_dns,
            "wan.settings.username": self.pppoe_user,
            "wan.settings.password": self.pppoe_password,
            "wan.settings.interface_name": self.pppoe_name,
        }
        self.on_inventory(controller.inventory)
        self.reload()

    # ------------------------------------------------------------ panels

    def _names_group(self) -> QGroupBox:
        box = QGroupBox(S.PORT_NAMES_GROUP)
        layout = QVBoxLayout(box)
        help_label = QLabel(S.PORT_NAMES_HELP)
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        self.port_names = QTableWidget(0, len(S.PORT_NAMES_COLUMNS))
        self.port_names.setHorizontalHeaderLabels(S.PORT_NAMES_COLUMNS)
        self.port_names.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.port_names.verticalHeader().hide()
        self.port_names.setMaximumHeight(300)
        self.port_names.itemChanged.connect(self._port_renamed)
        layout.addWidget(self.port_names)
        self.port_name_error = QLabel()
        self.port_name_error.setProperty("role", "danger")
        self.port_name_error.setWordWrap(True)
        self.port_name_error.hide()
        layout.addWidget(self.port_name_error)
        return box

    def _dhcp_panel(self) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
        self.dhcp_peer_dns = QCheckBox("use-peer-dns")
        self.dhcp_peer_dns.toggled.connect(
            lambda on: self.push_value("wan.settings.use_peer_dns", on))
        self.dhcp_default_route = QCheckBox("add-default-route")
        self.dhcp_default_route.toggled.connect(
            lambda on: self.push_value("wan.settings.add_default_route", on))
        form.addRow(self.dhcp_peer_dns)
        form.addRow(self.dhcp_default_route)
        return panel

    def _static_panel(self) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
        self.static_address = QLineEdit()
        self.static_address.setPlaceholderText("203.0.113.10/29")
        self.static_address.textEdited.connect(lambda text: self.push_parsed(
            "wan.settings.address", lambda: parse_cidr(text)))
        self.static_gateway = QLineEdit()
        self.static_gateway.textEdited.connect(lambda text: self.push_parsed(
            "wan.settings.gateway", lambda: parse_ip(text)))
        self.static_dns = QLineEdit()
        self.static_dns.textEdited.connect(lambda text: self.push_parsed(
            "wan.settings.dns_servers", lambda: parse_ip_list(text)))
        form.addRow(S.WAN_ADDRESS, self.static_address)
        form.addRow(S.WAN_GATEWAY, self.static_gateway)
        form.addRow(S.WAN_DNS, self.static_dns)
        return panel

    def _pppoe_panel(self) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
        self.pppoe_user = QLineEdit()
        self.pppoe_user.textEdited.connect(
            lambda text: self.push_value("wan.settings.username", text))
        self.pppoe_password = mark_secret(QLineEdit())
        self.pppoe_password.textEdited.connect(
            lambda text: self.push_value("wan.settings.password", SecretStr(text)))
        self.pppoe_name = QLineEdit()
        self.pppoe_name.textEdited.connect(
            lambda text: self.push_value("wan.settings.interface_name", text))
        form.addRow(S.WAN_PPPOE_USER, self.pppoe_user)
        form.addRow(S.WAN_PPPOE_PASSWORD, self.pppoe_password)
        form.addRow(S.WAN_PPPOE_NAME, self.pppoe_name)
        return panel

    # ------------------------------------------------------------ events

    def _apply_preset(self) -> None:
        mode = Mode(self.mode.currentData())
        ports = self.controller.ports()
        self.controller.edit(lambda profile: apply_preset(profile, mode, ports))

    def _port_renamed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 1:
            return
        default = item.data(Qt.ItemDataRole.UserRole)
        rows = {d: name for d, name, _ in self.controller.port_rows()}
        old, name = rows[default], item.text().strip()
        other = next((d for d, n in rows.items() if n == name and d != default), None)
        error = (S.PORT_NAME_EMPTY if not name
                 else S.PORT_NAME_TAKEN.format(name=name, port=other) if other else "")
        self.port_name_error.setText(error)
        self.port_name_error.setVisible(bool(error))
        keep = old if error or name == old else name
        if keep != item.text():
            self._loading = True  # setText fires itemChanged again
            try:
                item.setText(keep)
            finally:
                self._loading = False
        if keep == old:
            return
        self.push(lambda profile: rename_port(profile, default, name))
        self._loading = True  # own edits do not come back through load()
        try:
            self._fill_wan_ports()
        finally:
            self._loading = False

    def _fill_port_names(self) -> None:
        rows = self.controller.port_rows()
        self.port_names.setRowCount(len(rows))
        for row, (default, name, device) in enumerate(rows):
            cells = [default, name, device or "—"]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, default)
                if column != 1:
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.port_names.setItem(row, column, item)

    def _interface_changed(self, _index: int) -> None:
        self.push_value("wan.interface", self.wan_interface.currentData())

    def _vlan_toggled(self, on: bool) -> None:
        self.push_value("wan.vlan_id", self.wan_vlan_id.value() if on else None)

    def _vlan_id_changed(self, value: int) -> None:
        if self.wan_vlan_enabled.isChecked():
            self.push_value("wan.vlan_id", value)

    def _type_changed(self, index: int) -> None:
        self.settings_stack.setCurrentIndex(index)
        wan_type = WanType(self.wan_type.currentData())
        self.push(lambda profile: profile.model_copy(
            update={"wan": profile.wan.with_type(wan_type)}))

    def on_inventory(self, inventory) -> None:
        self.port_name_error.hide()
        self._fill_port_names()
        self._fill_wan_ports()

    def _fill_wan_ports(self) -> None:
        current = self.controller.profile.wan.interface
        self.wan_interface.clear()
        self.wan_interface.addItem("—", None)
        for port in self.controller.ports():
            self.wan_interface.addItem(port, port)
        if current and self.wan_interface.findData(current) < 0:
            self.wan_interface.addItem(current, current)
        self.wan_interface.setCurrentIndex(max(self.wan_interface.findData(current), 0))

    def load(self, profile: DeviceProfile) -> None:
        self.identity.setText(profile.identity)
        self.mode.setCurrentIndex(self.mode.findData(profile.mode.value))
        self._fill_port_names()
        wan = profile.wan
        self.wan_enabled.setChecked(wan.enabled)
        index = self.wan_interface.findData(wan.interface)
        if index < 0 and wan.interface:
            self.wan_interface.addItem(wan.interface, wan.interface)
            index = self.wan_interface.findData(wan.interface)
        self.wan_interface.setCurrentIndex(max(index, 0))
        self.wan_vlan_enabled.setChecked(wan.vlan_id is not None)
        self.wan_vlan_id.setValue(wan.vlan_id or CETIN_VLAN_ID)
        self.wan_vlan_name.setText(wan.vlan_name)
        self.wan_vlan_name.setPlaceholderText(f"vlan{wan.vlan_id or CETIN_VLAN_ID}")
        type_index = self.wan_type.findData(wan.type.value)
        self.wan_type.setCurrentIndex(type_index)
        self.settings_stack.setCurrentIndex(type_index)
        settings = wan.settings
        if isinstance(settings, DhcpWan):
            self.dhcp_peer_dns.setChecked(settings.use_peer_dns)
            self.dhcp_default_route.setChecked(settings.add_default_route)
        elif isinstance(settings, StaticWan):
            self.static_address.setText(str(settings.address))
            self.static_gateway.setText(str(settings.gateway))
            self.static_dns.setText(",".join(str(d) for d in settings.dns_servers))
        elif isinstance(settings, PppoeWan):
            self.pppoe_user.setText(settings.username)
            self.pppoe_password.setText(settings.password.get_secret_value())
            self.pppoe_name.setText(settings.interface_name)
        self.wan_list.setChecked(wan.add_to_wan_list)
        self.wan_mss.setChecked(wan.mss_clamping)
        self.wan_nat.setChecked(wan.nat_masquerade)

    def refresh_state(self, profile: DeviceProfile) -> None:
        for widget in (self.wan_interface, self.wan_vlan_enabled, self.wan_type,
                       self.settings_stack, self.wan_list, self.wan_mss, self.wan_nat):
            widget.setEnabled(profile.wan.enabled)
        tagged = profile.wan.enabled and profile.wan.vlan_id is not None
        self.wan_vlan_id.setEnabled(tagged)
        self.wan_vlan_name.setEnabled(tagged)
