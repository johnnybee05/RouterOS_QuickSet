"""LAN / Bridge: bridge options, protocol-mode, members, HW offload, LAN IP, DHCP server."""

from __future__ import annotations

from PySide6.QtCore import Qt
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
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mqs.capabilities.offload import OffloadPlan
from mqs.messages_cs import format_message
from mqs.model.bridge import FrameTypes, HwOffloadMode, LanAddressing, ProtocolMode
from mqs.model.network import Ipv4Range
from mqs.model.profile import DeviceProfile
from mqs.ui import strings_cs as S
from mqs.ui.controller import Analysis, ProfileController
from mqs.ui.pages.base import (
    InputError,
    Page,
    parse_cidr,
    parse_ip,
    parse_ip_list,
    parse_optional_int,
    parse_optional_ip,
)
from mqs.ui.widgets.common import PortSelector


class LanBridgePage(Page):
    def __init__(self, controller: ProfileController, parent: QWidget | None = None) -> None:
        super().__init__(controller, parent)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        layout.addWidget(self._bridge_group())
        layout.addWidget(self._offload_group())
        layout.addWidget(self._lan_group())
        layout.addWidget(self._dhcp_group())
        layout.addStretch(1)

        self.fields = {
            "bridge.name": self.bridge_name,
            "bridge.protocol_mode": self.protocol,
            "bridge.ports": self.ports,
            "bridge.hw_offload": self.offload_mode,
            "bridge.use_ip_firewall": self.ip_firewall,
            "bridge.priority": self.priority,
            "bridge.ageing_time_s": self.ageing,
            "lan.address": self.lan_address,
            "lan.dhcp_server.enabled": self.dhcp_enabled,
            "lan.dhcp_server.pool": self.pool_start,
            "lan.dhcp_server.gateway": self.dhcp_gateway,
            "lan.dhcp_server.dns_servers": self.dhcp_dns,
        }
        self.reload()

    # ------------------------------------------------------------ groups

    def _bridge_group(self) -> QGroupBox:
        box = QGroupBox(S.BRIDGE_GROUP)
        form = QFormLayout(box)
        self.bridge_name = QLineEdit()
        self.bridge_name.textEdited.connect(lambda t: self.push_value("bridge.name", t))
        form.addRow(S.BRIDGE_NAME, self.bridge_name)

        self.protocol = QComboBox()
        for mode in ProtocolMode:
            self.protocol.addItem(mode.value, mode)
        self.protocol.currentIndexChanged.connect(
            lambda _: self.push_value("bridge.protocol_mode", self.protocol.currentData()))
        form.addRow(S.BRIDGE_PROTOCOL, self.protocol)

        self.priority = QLineEdit()
        self.priority.setPlaceholderText("0x8000")
        self.priority.textEdited.connect(lambda t: self.push_parsed(
            "bridge.priority", lambda: parse_optional_int(t, 0, 65535)))
        form.addRow(S.BRIDGE_PRIORITY, self.priority)
        self.ageing = QLineEdit()
        self.ageing.textEdited.connect(lambda t: self.push_parsed(
            "bridge.ageing_time_s", lambda: parse_optional_int(t, 1, 10**6)))
        form.addRow(S.BRIDGE_AGEING, self.ageing)

        vlan_row = QHBoxLayout()
        self.vlan_filtering = QCheckBox(S.BRIDGE_VLAN)
        self.vlan_filtering.toggled.connect(lambda on: self.push_value("bridge.vlan_filtering", on))
        self.pvid = QSpinBox()
        self.pvid.setRange(1, 4094)
        self.pvid.valueChanged.connect(lambda v: self.push_value("bridge.pvid", v))
        self.frame_types = QComboBox()
        for frame in FrameTypes:
            self.frame_types.addItem(frame.value, frame)
        self.frame_types.currentIndexChanged.connect(
            lambda _: self.push_value("bridge.frame_types", self.frame_types.currentData()))
        vlan_row.addWidget(self.vlan_filtering)
        vlan_row.addWidget(QLabel(S.BRIDGE_PVID))
        vlan_row.addWidget(self.pvid)
        vlan_row.addWidget(QLabel(S.BRIDGE_FRAME_TYPES))
        vlan_row.addWidget(self.frame_types, 1)
        form.addRow(vlan_row)

        self.ip_firewall = QCheckBox(S.BRIDGE_IP_FIREWALL)
        self.ip_firewall.toggled.connect(lambda on: self.push_value("bridge.use_ip_firewall", on))
        form.addRow(self.ip_firewall)

        self.ports = PortSelector()
        self.ports.setMinimumHeight(140)
        self.ports.selectionChanged2.connect(lambda ports: self.push_value("bridge.ports", ports))
        form.addRow(QLabel(S.BRIDGE_PORTS))
        form.addRow(self.ports)
        return box

    def _offload_group(self) -> QGroupBox:
        box = QGroupBox(S.OFFLOAD_GROUP)
        layout = QVBoxLayout(box)
        help_label = QLabel(S.OFFLOAD_HELP)
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        self.offload_mode = QComboBox()
        for value, label in S.OFFLOAD_MODES.items():
            self.offload_mode.addItem(label, HwOffloadMode(value))
        self.offload_mode.currentIndexChanged.connect(
            lambda _: self.push_value("bridge.hw_offload", self.offload_mode.currentData()))
        layout.addWidget(self.offload_mode)

        conflict_row = QHBoxLayout()
        self.conflict_label = QLabel()
        self.conflict_label.setProperty("role", "danger")
        self.conflict_label.setWordWrap(True)
        self.switch_to_rstp = QPushButton(S.OFFLOAD_SWITCH_TO_RSTP)
        self.switch_to_rstp.clicked.connect(
            lambda: self.controller.edit(lambda p: p.model_copy(update={
                "bridge": p.bridge.model_copy(update={"protocol_mode": ProtocolMode.RSTP})})))
        conflict_row.addWidget(self.conflict_label, 1)
        conflict_row.addWidget(self.switch_to_rstp)
        layout.addLayout(conflict_row)
        self.conflict_label.hide()
        self.switch_to_rstp.hide()

        self.groups_label = QLabel()
        self.groups_label.setWordWrap(True)
        layout.addWidget(self.groups_label)
        self.offload_table = QTableWidget(0, len(S.OFFLOAD_COLUMNS))
        self.offload_table.setHorizontalHeaderLabels(S.OFFLOAD_COLUMNS)
        self.offload_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.offload_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch)
        self.offload_table.verticalHeader().hide()
        self.offload_table.setMinimumHeight(160)
        layout.addWidget(self.offload_table)
        return box

    def _lan_group(self) -> QGroupBox:
        box = QGroupBox(S.LAN_GROUP)
        form = QFormLayout(box)
        self.addressing = QComboBox()
        for value, label in S.LAN_ADDRESSINGS.items():
            self.addressing.addItem(label, LanAddressing(value))
        self.addressing.currentIndexChanged.connect(
            lambda _: self.push_value("lan.addressing", self.addressing.currentData()))
        form.addRow(S.LAN_ADDRESSING, self.addressing)
        self.lan_address = QLineEdit()
        self.lan_address.setPlaceholderText("192.168.88.1/24")
        self.lan_address.textEdited.connect(lambda t: self.push_parsed(
            "lan.address", lambda: parse_cidr(t) if t.strip() else None))
        form.addRow(S.LAN_ADDRESS, self.lan_address)
        return box

    def _dhcp_group(self) -> QGroupBox:
        box = QGroupBox(S.DHCP_GROUP)
        form = QFormLayout(box)
        self.dhcp_enabled = QCheckBox(S.DHCP_ENABLED)
        self.dhcp_enabled.toggled.connect(
            lambda on: self.push_value("lan.dhcp_server.enabled", on))
        form.addRow(self.dhcp_enabled)
        self.pool_start = QLineEdit()
        self.pool_end = QLineEdit()
        self.pool_start.textEdited.connect(lambda _: self._pool_edited())
        self.pool_end.textEdited.connect(lambda _: self._pool_edited())
        form.addRow(S.DHCP_POOL_START, self.pool_start)
        form.addRow(S.DHCP_POOL_END, self.pool_end)
        self.dhcp_gateway = QLineEdit()
        self.dhcp_gateway.textEdited.connect(lambda t: self.push_parsed(
            "lan.dhcp_server.gateway", lambda: parse_optional_ip(t)))
        form.addRow(S.DHCP_GATEWAY, self.dhcp_gateway)
        self.dhcp_dns = QLineEdit()
        self.dhcp_dns.textEdited.connect(lambda t: self.push_parsed(
            "lan.dhcp_server.dns_servers", lambda: parse_ip_list(t)))
        form.addRow(S.DHCP_DNS, self.dhcp_dns)
        self.lease = QSpinBox()
        self.lease.setRange(60, 365 * 86400)
        self.lease.valueChanged.connect(
            lambda v: self.push_value("lan.dhcp_server.lease_time_s", v))
        form.addRow(S.DHCP_LEASE, self.lease)
        self.dhcp_name = QLineEdit()
        self.dhcp_name.textEdited.connect(lambda t: self.push_value("lan.dhcp_server.name", t))
        form.addRow(S.DHCP_NAME, self.dhcp_name)
        self.dhcp_comment = QLineEdit()
        self.dhcp_comment.textEdited.connect(
            lambda t: self.push_value("lan.dhcp_server.comment", t))
        form.addRow(S.DHCP_COMMENT, self.dhcp_comment)
        return box

    def _pool_edited(self) -> None:
        def parse() -> Ipv4Range:
            start, end = parse_ip(self.pool_start.text()), parse_ip(self.pool_end.text())
            if start > end:
                raise InputError(S.INVALID_RANGE)
            return Ipv4Range(start=start, end=end)

        self.push_parsed("lan.dhcp_server.pool", parse)

    # ------------------------------------------------------------ refresh

    def _refresh_ports(self, profile: DeviceProfile) -> None:
        wan = profile.wan.interface if profile.wan.enabled else None
        ports = self.controller.ports()
        for port in profile.bridge.ports:
            if port not in ports:
                ports.append(port)
        locked = {wan: S.WAN_PORT_LOCKED} if wan else {}
        self.ports.set_ports(ports, profile.bridge.ports, locked)

    def on_inventory(self, inventory) -> None:
        self._refresh_ports(self.controller.profile)

    def load(self, profile: DeviceProfile) -> None:
        bridge, lan, dhcp = profile.bridge, profile.lan, profile.lan.dhcp_server
        self.bridge_name.setText(bridge.name)
        self.protocol.setCurrentIndex(self.protocol.findData(bridge.protocol_mode))
        self.priority.setText("" if bridge.priority is None else f"0x{bridge.priority:04X}")
        self.ageing.setText("" if bridge.ageing_time_s is None else str(bridge.ageing_time_s))
        self.vlan_filtering.setChecked(bridge.vlan_filtering)
        self.pvid.setValue(bridge.pvid)
        self.frame_types.setCurrentIndex(self.frame_types.findData(bridge.frame_types))
        self.pvid.setEnabled(bridge.vlan_filtering)
        self.frame_types.setEnabled(bridge.vlan_filtering)
        self.ip_firewall.setChecked(bridge.use_ip_firewall)
        self.offload_mode.setCurrentIndex(self.offload_mode.findData(bridge.hw_offload))
        self._refresh_ports(profile)
        self.addressing.setCurrentIndex(self.addressing.findData(lan.addressing))
        self.lan_address.setText(str(lan.address) if lan.address else "")
        self.lan_address.setEnabled(lan.addressing == LanAddressing.STATIC)
        self.dhcp_enabled.setChecked(dhcp.enabled)
        self.pool_start.setText(str(dhcp.pool.start))
        self.pool_end.setText(str(dhcp.pool.end))
        self.dhcp_gateway.setText(str(dhcp.gateway) if dhcp.gateway else "")
        self.dhcp_dns.setText(",".join(str(d) for d in dhcp.dns_servers))
        self.lease.setValue(dhcp.lease_time_s)
        self.dhcp_name.setText(dhcp.name)
        self.dhcp_comment.setText(dhcp.comment)

    def on_analysis(self, analysis: Analysis) -> None:
        self.show_offload(analysis.primary.offload)

    def show_offload(self, plan: OffloadPlan) -> None:
        conflict = next((i for i in plan.issues if i.code == "offload.mstp_conflict"), None)
        self.conflict_label.setVisible(conflict is not None)
        self.switch_to_rstp.setVisible(conflict is not None)
        if conflict is not None:
            self.conflict_label.setText(conflict.message)
        summary = [i.message for i in plan.issues
                   if i.code in ("offload.software_bridge", "offload.mixed_software",
                                 "offload.version_too_old", "offload.no_device_data")]
        self.groups_label.setText("\n".join(summary))
        self.offload_table.setRowCount(len(plan.ports))
        for row, port in enumerate(plan.ports):
            chip = f"{port.switch} ({port.chip_type})" if port.switch else "—"
            cells = [port.port, chip, S.OFFLOAD_STATUS[port.status.value],
                     format_message(port.reason, port.params)]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.offload_table.setItem(row, column, item)
