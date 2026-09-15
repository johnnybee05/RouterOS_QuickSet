"""Základ: identity, scenario presets, WAN."""

from __future__ import annotations

from pydantic import SecretStr
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mqs.model.presets import apply_preset
from mqs.model.profile import DeviceProfile, Mode
from mqs.model.wan import DhcpWan, PppoeWan, StaticWan, WanType
from mqs.ui import strings_cs as S
from mqs.ui.controller import ProfileController
from mqs.ui.pages.base import Page, parse_cidr, parse_ip, parse_ip_list


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

        wan_box = QGroupBox(S.WAN_GROUP)
        form = QFormLayout(wan_box)
        self.wan_enabled = QCheckBox(S.WAN_ENABLED)
        self.wan_enabled.toggled.connect(lambda on: self.push_value("wan.enabled", on))
        form.addRow(self.wan_enabled)
        self.wan_interface = QComboBox()
        self.wan_interface.currentIndexChanged.connect(self._interface_changed)
        form.addRow(S.WAN_INTERFACE, self.wan_interface)
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
            "wan.interface": self.wan_interface,
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
        self.pppoe_password = QLineEdit()
        self.pppoe_password.setEchoMode(QLineEdit.EchoMode.Password)
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

    def _interface_changed(self, _index: int) -> None:
        self.push_value("wan.interface", self.wan_interface.currentData())

    def _type_changed(self, index: int) -> None:
        self.settings_stack.setCurrentIndex(index)
        wan_type = WanType(self.wan_type.currentData())
        self.push(lambda profile: profile.model_copy(
            update={"wan": profile.wan.with_type(wan_type)}))

    def on_inventory(self, inventory) -> None:
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
        wan = profile.wan
        self.wan_enabled.setChecked(wan.enabled)
        index = self.wan_interface.findData(wan.interface)
        if index < 0 and wan.interface:
            self.wan_interface.addItem(wan.interface, wan.interface)
            index = self.wan_interface.findData(wan.interface)
        self.wan_interface.setCurrentIndex(max(index, 0))
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
        for widget in (self.wan_interface, self.wan_type, self.settings_stack, self.wan_list,
                       self.wan_mss, self.wan_nat):
            widget.setEnabled(profile.wan.enabled)
