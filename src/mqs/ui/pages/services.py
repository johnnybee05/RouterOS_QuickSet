"""Služby a hardening: bound form fields + editable RouterOS block with highlighting."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mqs.generator.hardening import binding
from mqs.model.profile import DeviceProfile
from mqs.model.services import DEFAULT_HARDENING_V7
from mqs.ui import strings as S
from mqs.ui.controller import Analysis, ProfileController
from mqs.ui.highlighter import RouterOsHighlighter
from mqs.ui.pages.base import InputError, Page, parse_network, split_list
from mqs.ui.widgets.common import monospace_font

TIME_ZONES = ["Europe/Prague", "Europe/Bratislava", "Europe/Vienna", "Europe/Berlin", "UTC"]


def confirm_reset(parent: QWidget) -> bool:
    answer = QMessageBox.question(parent, S.WARNING_TITLE, S.SERVICES_RESET_CONFIRM)
    return answer == QMessageBox.StandardButton.Yes


class ServicesPage(Page):
    def __init__(self, controller: ProfileController, parent: QWidget | None = None,
                 confirm: Callable[[QWidget], bool] = confirm_reset, dark: bool = False) -> None:
        super().__init__(controller, parent)
        self.confirm = confirm
        layout = QVBoxLayout(self)

        fields_box = QGroupBox(S.SERVICES_FIELDS_GROUP)
        form = QFormLayout(fields_box)
        self.dns = QLineEdit()
        self.dns.textEdited.connect(lambda t: self._bind(binding.set_dns_servers, split_list(t)))
        self.ssh_port = QSpinBox()
        self.ssh_port.setRange(1, 65535)
        self.ssh_port.valueChanged.connect(lambda v: self._bind(binding.set_ssh_port, v))
        self.snmp = QLineEdit()
        self.snmp.textEdited.connect(
            lambda t: self._bind(binding.set_snmp_community, t.strip()) if t.strip() else None)
        self.ntp = QLineEdit()
        self.ntp.textEdited.connect(lambda t: self._bind(binding.set_ntp_servers, split_list(t)))
        self.tz = QComboBox()
        self.tz.setEditable(True)
        self.tz.addItems(TIME_ZONES)
        self.tz.currentTextChanged.connect(
            lambda t: self._bind(binding.set_time_zone, t.strip()) if t.strip() else None)
        self.dns_remote = QCheckBox(S.SERVICES_DNS_REMOTE)
        self.dns_remote.toggled.connect(
            lambda on: self.push_value("services.dns_remote_requests", on))
        dns_remote_help = QLabel(S.SERVICES_DNS_REMOTE_HELP)
        dns_remote_help.setWordWrap(True)
        form.addRow(S.SERVICES_DNS, self.dns)
        form.addRow("", self.dns_remote)
        form.addRow("", dns_remote_help)
        form.addRow(S.SERVICES_SSH_PORT, self.ssh_port)
        form.addRow(S.SERVICES_SNMP, self.snmp)
        form.addRow(S.SERVICES_NTP, self.ntp)
        form.addRow(S.SERVICES_TZ, self.tz)
        layout.addWidget(fields_box)

        self.access = QLabel()
        self.access.setWordWrap(True)
        layout.addWidget(self.access)

        editor_box = QGroupBox(S.SERVICES_EDITOR_GROUP)
        editor_layout = QVBoxLayout(editor_box)
        self.editor = QPlainTextEdit()
        self.editor.setFont(monospace_font())
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.highlighter = RouterOsHighlighter(self.editor.document(), dark)
        self.editor.textChanged.connect(self._editor_changed)
        editor_layout.addWidget(self.editor)
        buttons = QHBoxLayout()
        self.reset_button = QPushButton(S.SERVICES_RESET)
        self.reset_button.clicked.connect(self._reset)
        self.robust_logging = QCheckBox(S.SERVICES_ROBUST_LOGGING)
        self.robust_logging.toggled.connect(
            lambda on: self.push_value("services.robust_logging", on))
        buttons.addWidget(self.robust_logging, 1)
        buttons.addWidget(self.reset_button)
        editor_layout.addLayout(buttons)
        layout.addWidget(editor_box, 1)

        ext_box = QGroupBox(S.SERVICES_EXTENSIONS_GROUP)
        ext = QFormLayout(ext_box)
        self.cert_enabled = QCheckBox(S.SERVICES_CERTIFICATE)
        self.certificate = QComboBox()
        self.certificate.setEditable(True)
        self.cert_enabled.toggled.connect(lambda _: self._certificate_changed())
        self.certificate.currentTextChanged.connect(lambda _: self._certificate_changed())
        ext.addRow(self.cert_enabled, self.certificate)
        self.subnet_enabled = QCheckBox(S.SERVICES_MGMT_SUBNET)
        self.subnet = QLineEdit()
        self.subnet.setPlaceholderText("10.0.0.0/24")
        self.subnet_enabled.toggled.connect(lambda _: self._subnet_changed())
        self.subnet.textEdited.connect(lambda _: self._subnet_changed())
        ext.addRow(self.subnet_enabled, self.subnet)
        layout.addWidget(ext_box)

        self.fields = {"services.hardening_text": self.editor,
                       "services.dns_remote_requests": self.dns_remote,
                       "services.management_subnet": self.subnet}
        self.reload()

    # ------------------------------------------------------------ binding

    def _bind(self, setter: Callable[[str, object], str], value: object) -> None:
        if self._loading:
            return
        text = setter(self.editor.toPlainText(), value)
        self._set_editor_text(text)
        self.push_value("services.hardening_text", text)

    def _set_editor_text(self, text: str) -> None:
        if self.editor.toPlainText() == text:
            return
        was_loading, self._loading = self._loading, True
        scroll = self.editor.verticalScrollBar().value()
        self.editor.setPlainText(text)
        self.editor.verticalScrollBar().setValue(scroll)
        self._loading = was_loading

    def _editor_changed(self) -> None:
        if self._loading:
            return
        text = self.editor.toPlainText()
        self._load_fields(text)
        self.push_value("services.hardening_text", text)

    def _load_fields(self, text: str) -> None:
        fields = binding.read_fields(text)
        was_loading, self._loading = self._loading, True
        try:
            if not self.dns.hasFocus():
                self.dns.setText(",".join(fields.dns_servers or ()))
            if fields.ssh_port is not None and not self.ssh_port.hasFocus():
                self.ssh_port.setValue(fields.ssh_port)
            if not self.snmp.hasFocus():
                self.snmp.setText(fields.snmp_community or "")
            if not self.ntp.hasFocus():
                self.ntp.setText(",".join(fields.ntp_servers or ()))
            if fields.time_zone and not self.tz.hasFocus():
                self.tz.setCurrentText(fields.time_zone)
        finally:
            self._loading = was_loading

    def _reset(self) -> None:
        if not self.confirm(self):
            return
        self._set_editor_text(DEFAULT_HARDENING_V7)
        self._load_fields(DEFAULT_HARDENING_V7)
        self.push_value("services.hardening_text", DEFAULT_HARDENING_V7)

    def _certificate_changed(self) -> None:
        name = self.certificate.currentText().strip()
        self.certificate.setEnabled(self.cert_enabled.isChecked())
        self.push_value("services.api_ssl_certificate",
                        name if self.cert_enabled.isChecked() and name else None)

    def _subnet_changed(self) -> None:
        self.subnet.setEnabled(self.subnet_enabled.isChecked())
        if not self.subnet_enabled.isChecked():
            self.syntax_errors.pop("services.management_subnet", None)
            self.push_value("services.management_subnet", None)
            self._refresh_field_errors()
            return

        def parse():
            if not self.subnet.text().strip():
                raise InputError(S.INVALID_NETWORK)
            return parse_network(self.subnet.text())

        self.push_parsed("services.management_subnet", parse)

    # ------------------------------------------------------------ refresh

    def set_dark(self, dark: bool) -> None:
        self.highlighter.set_dark(dark)

    def on_inventory(self, inventory) -> None:
        current = self.certificate.currentText()
        self.certificate.clear()
        if inventory is not None:
            self.certificate.addItems(inventory.certificates)
        self.certificate.setCurrentText(current)

    def load(self, profile: DeviceProfile) -> None:
        services = profile.services
        self._set_editor_text(services.hardening_text)
        self._load_fields(services.hardening_text)
        self.dns_remote.setChecked(services.dns_remote_requests)
        self.robust_logging.setChecked(services.robust_logging)
        self.cert_enabled.setChecked(services.api_ssl_certificate is not None)
        self.certificate.setCurrentText(services.api_ssl_certificate or "")
        self.certificate.setEnabled(services.api_ssl_certificate is not None)
        self.subnet_enabled.setChecked(services.management_subnet is not None)
        self.subnet.setText(str(services.management_subnet or ""))
        self.subnet.setEnabled(services.management_subnet is not None)

    def refresh_state(self, profile: DeviceProfile) -> None:
        # Cannot be switched on without the basic filter, but can always be switched off.
        self.dns_remote.setEnabled(
            profile.firewall.basic_filter or profile.services.dns_remote_requests)

    def on_analysis(self, analysis: Analysis) -> None:
        access = analysis.primary.access
        if access.blocked:
            self.access.setText(S.ACCESS_BLOCKED)
            self.access.setProperty("role", "danger")
        else:
            paths = ", ".join(f"{n} {access.services[n].port}" for n in access.remaining)
            text = S.ACCESS_OK.format(paths=paths)
            disabled = [i.message for i in access.issues if i.code == "access.path_disabled"]
            if disabled:
                text += "\n" + "\n".join(disabled)
            self.access.setText(text)
            self.access.setProperty("role", "danger" if disabled else "ok")
        self.access.style().unpolish(self.access)
        self.access.style().polish(self.access)
