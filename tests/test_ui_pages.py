"""Page and main window smoke tests with fake dialogs, transports and orchestrator."""

from __future__ import annotations

from datetime import datetime
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import ClassVar

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLineEdit

from factories import make_inventory, make_npk
from fake_transport import FakeTransport
from mqs.app import parse_args, prefill
from mqs.apply.orchestrator import ApplyReport
from mqs.apply.plan import DEFAULT_PASSWORD, ApplyMode, make_names
from mqs.apply.preflight import ExistingObjects
from mqs.apply.update import plan_from_dir
from mqs.apply.verify import CheckResult
from mqs.apply.version_change import VersionReport
from mqs.model.bridge import FrameTypes, HwOffloadMode, LanAddressing, ProtocolMode
from mqs.model.inventory import Ipv6PackageState, PackageInfo
from mqs.model.wan import CETIN_VLAN_ID, WanType
from mqs.profiles.store import ProfileStore
from mqs.transport.base import (
    AuthError,
    ConnectionLost,
    ConnectionParams,
    KeyPassphraseError,
)
from mqs.transport.factory import ConnectOutcome, TransportChoice
from mqs.transport.mac_connect import MacConnectMethod, MacConnectResult
from mqs.transport.mndp import TlvType, decode, device_from_packet, encode
from mqs.transport.netifs import LocalInterface
from mqs.ui import strings_cs as S
from mqs.ui.main_window import MainWindow
from mqs.ui.pages.base import parse_cidr, parse_optional_ip
from mqs.ui.pages.device import describe_error
from mqs.ui.settings import SavedLogin, SettingsStore

pytestmark = pytest.mark.qt

TEN_PORTS = [f"ether{i}" for i in range(1, 11)]
LAN_INTERFACE = LocalInterface("Ethernet", IPv4Address("192.168.88.254"),
                               IPv4Network("192.168.88.0/24"), index=12)


class FakeDialogs:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.errors: list[str] = []
        self.answers: dict[str, object] = {}
        self.dry_runs: list[str] = []

    def error(self, parent, text):
        self.errors.append(text)

    def info(self, parent, text):
        pass

    def question(self, parent, title, text):
        return bool(self.answers.get("question", False))

    def ask_text(self, parent, title, label, text=""):
        return self.answers.get("text")

    def ask_item(self, parent, title, label, items):
        return self.answers.get("item")

    def open_file(self, parent, title, file_filter):
        return self.answers.get("open")

    def save_file(self, parent, title, suggested, file_filter):
        return self.tmp_path / suggested

    def choose_directory(self, parent, title, start):
        return self.tmp_path

    def confirm_dry_run(self, parent, script, issues):
        self.dry_runs.append(script)
        return bool(self.answers.get("dry_run", True))

    def ask_reset_identity(self, parent, identity, *, keep_users=False):
        self.reset_keep_users = keep_users
        return self.answers.get("identity", identity)

    def ask_version_identity(self, parent, identity, *, version, current, reboots):
        self.version_asked = {"identity": identity, "version": version, "current": current,
                              "reboots": reboots}
        return self.answers.get("identity", identity)


class FakeScanner:
    def __init__(self):
        self.errors = ["USB 10.1.1.5: address in use"]
        self.opened = self.closed = False
        self.solicits = 0
        packet = decode(encode([(TlvType.MAC, b"\x01\x02\x03\x04\x05\x06"),
                                (TlvType.IDENTITY, b"hap"), (TlvType.IPV4, b"\xc0\xa8\x58\x01"),
                                (TlvType.INTERFACE, b"ether1")]))
        self.device = device_from_packet(packet, source_ip="192.168.88.1", local=LAN_INTERFACE,
                                         now=0)

    def open(self):
        self.opened = True

    def solicit(self):
        self.solicits += 1

    def poll(self, timeout):
        return [self.device]

    def devices(self, max_age=None):
        return [self.device]

    def close(self):
        self.closed = True


def two_chip_inventory():
    """QCA8337: documented as not offloading MSTP, which the conflict test relies on."""
    port_switch = {f"ether{i}": "switch1" for i in range(1, 6)}
    port_switch |= {f"ether{i}": "switch2" for i in range(6, 11)}
    return make_inventory("7.15.3", TEN_PORTS, switches={"switch1": "QCA8337",
                                                         "switch2": "QCA8337"},
                          port_switch=port_switch, board_name="RB3011UiAS-RM")


class RecordingOrchestrator:
    instances: ClassVar[list[RecordingOrchestrator]] = []

    def __init__(self, ctx):
        self.ctx = ctx
        self.confirmed = False
        RecordingOrchestrator.instances.append(self)

    def run(self):
        names = make_names(self.ctx.now(), False)
        report = ApplyReport(names=names, transport=self.ctx.transport,
                             checks=[CheckResult("verify.identity", True,
                                                 {"expected": "R", "actual": "R"})],
                             temp_address=self.ctx.temp_address)
        report.rollback_at = None if self.ctx.options.rollback_minutes is None else self.ctx.now()
        report.succeeded = True
        return report

    def confirm(self):
        self.confirmed = True


def scrambled(data: bytes) -> bytes:
    return bytes(b ^ 0x5A for b in data)


def settings_store(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path / "settings", protect=scrambled, unprotect=scrambled)


@pytest.fixture
def window(qtbot, tmp_path):
    dialogs = FakeDialogs(tmp_path)
    transport = FakeTransport({})
    inventory = two_chip_inventory()
    connect_calls = []

    def connect_fn(params, choice, **kwargs):
        connect_calls.append((params, choice, kwargs))
        if params.password == "wrong":
            raise AuthError("bad")
        transport.connect()
        return ConnectOutcome(transport)

    mac_calls = []

    def mac_connect_fn(device, params, address, interface, **kwargs):
        mac_calls.append({"device": device, "params": params, "address": address,
                          "interface": interface, **kwargs})
        transport.connect()
        return MacConnectResult(
            ConnectOutcome(transport), MacConnectMethod.MAC_TELNET,
            temp_address=f"{address}/{interface.network.prefixlen}",
            device_interface=device.interface_name,
        )

    RecordingOrchestrator.instances.clear()
    win = MainWindow(
        dialogs=dialogs,
        store=ProfileStore(tmp_path / "profiles"),
        settings_store=settings_store(tmp_path),
        device_kwargs={
            "connect_fn": connect_fn,
            "discover_fn": lambda t, on_step: inventory,
            "probe_fn": lambda t, v: {"fasttrack_hw_offload": True},
            "session_port_fn": lambda t, host: "ether3",
            "scanner_factory": FakeScanner,
            "mac_connect_fn": mac_connect_fn,
            "local_interfaces_fn": lambda: [LAN_INTERFACE],
            "propose_fn": lambda interface, used: IPv4Address("192.168.88.253"),
        },
        apply_kwargs={
            "orchestrator_factory": RecordingOrchestrator,
            "collect_existing_fn": lambda t: ExistingObjects(),
            "uploader_factory_builder": lambda session, options: (lambda t: None),
            "update_dir": tmp_path / "Update",
        },
    )
    qtbot.addWidget(win)
    win.test_dialogs = dialogs
    win.test_transport = transport
    win.test_connect_calls = connect_calls
    win.test_mac_calls = mac_calls
    return win


def connect(win, qtbot, password="pw"):
    page = win.device_page
    page.host.setText("192.168.88.1")
    page.password.setText(password)
    page.connect_device()
    qtbot.waitUntil(lambda: not win.runner.busy, timeout=5000)
    win.controller.recompute()


def test_window_structure_and_navigation(window):
    # Checked against the page table rather than against a count, so adding a page is a
    # one-line change in one place instead of a number to find in a test.
    from mqs.ui import strings_cs as S

    keys = list(window.pages)
    assert window.nav.count() == len(keys) and window.stack.count() == len(keys)
    labels = [window.nav.item(row).text() for row in range(window.nav.count())]
    assert labels == [S.NAV[key] for key in keys]
    window.nav.setCurrentRow(keys.index("preview"))
    assert window.stack.currentWidget() is window.preview_page
    assert window.dialect_badge.text().startswith("Offline")
    assert window.controller.profile.mode.value == "router"


def test_window_title_shows_version(window):
    from mqs import __version__

    assert window.windowTitle() == f"{S.APP_TITLE} {__version__}"


def test_connect_updates_status_badge_and_ports(window, qtbot):
    connect(window, qtbot)
    session = window.device_page.session
    assert session is not None and session.session_port == "ether3"
    assert window.test_connect_calls[0][2]["api_port"] == 8729
    assert "RouterOS v7" in window.dialect_badge.text()
    assert "192.168.88.1" in window.status_connection.text()
    assert "nešifrovaná identita" in window.status_security.text()
    assert window.device_page.ports_table.rowCount() == 10
    assert window.device_page.info_labels["board"].text() == "RB3011UiAS-RM"
    notes = window.device_page.device_notes
    assert notes.isVisibleTo(window.device_page) and "QCA8337" in notes.text()
    assert window.controller.ports() == TEN_PORTS
    window.device_page.disconnect_device()
    assert window.status_connection.text() == "Odpojeno"


def test_connect_auth_failure_shows_error(window, qtbot):
    connect(window, qtbot, password="wrong")
    assert window.device_page.session is None
    assert "Přihlášení selhalo" in window.test_dialogs.errors[-1]


def test_scan_fills_table_and_double_click_uses_ip(window):
    page = window.device_page
    page.toggle_scan()
    assert "address in use" in page.scan_hint.text()
    page.scan_tick()
    assert page.scan_table.rowCount() == 1 and page.scan_table.item(0, 0).text() == "hap"
    page.use_scanned_device(0)
    assert page.host.text() == "192.168.88.1"
    scanner = page._scanner
    page.toggle_scan()
    assert scanner.closed and page._scanner is None


def test_connect_by_mac_needs_a_selected_device(window):
    page = window.device_page
    assert not page.mac_button.isEnabled()
    page.connect_by_mac_clicked()  # no selection: no-op
    assert window.test_mac_calls == []
    page.toggle_scan()
    page.scan_tick()
    page.scan_table.selectRow(0)
    assert page.mac_button.isEnabled()


def test_connect_by_mac_confirms_then_connects(window, qtbot):
    page = window.device_page
    page.toggle_scan()
    page.scan_tick()
    page.scan_table.selectRow(0)
    page.password.setText("pw")
    window.test_dialogs.answers["question"] = False  # user declines
    page.connect_by_mac_clicked()
    assert window.test_mac_calls == [] and page.session is None

    window.test_dialogs.answers["question"] = True
    page.connect_by_mac_clicked()
    qtbot.waitUntil(lambda: page.session is not None and not window.runner.busy, timeout=5000)
    call = window.test_mac_calls[0]
    assert call["device"].mac == "01:02:03:04:05:06"
    assert str(call["address"]) == "192.168.88.253"
    assert call["interface"] is LAN_INTERFACE
    assert call["params"].password == "pw"
    assert (call["api_port"], call["ssh_port"]) == (page.api_port.value(), page.ssh_port.value())
    session = page.session
    assert session.temp_address == "192.168.88.253/24"
    assert session.mac_method == "mac-telnet"
    assert session.params.host == "192.168.88.253"
    # Named by the MAC it was reached by; the lent address is only how the API gets there.
    assert page.connect_status.text().startswith("Připojeno: 01:02:03:04:05:06 (přes mac-telnet")
    assert "mqs-temp" in page.connect_status.text()
    assert window.controller.inventory is not None
    assert window.status_connection.text() == (
        "Připojeno: 01:02:03:04:05:06 (přes mac-telnet; api-ssl:8729 na 192.168.88.253)")


def test_connect_by_mac_cancelled_during_the_login_gives_the_address_back(window, qtbot):
    """2026-09-25, v6 hEX S: "Zrušit" pressed while MAC-Telnet logged in closed the new
    connection under the task, the window said "api-ssl transport is not connected" and
    the lent address stayed on the device."""
    page = window.device_page
    inner = page.mac_connect_fn

    def cancelled_meanwhile(*args, **kwargs):
        result = inner(*args, **kwargs)
        page.cancel_connect()  # the user, while the login was still running
        return result

    page.mac_connect_fn = cancelled_meanwhile
    page.toggle_scan()
    page.scan_tick()
    page.scan_table.selectRow(0)
    window.test_dialogs.answers["question"] = True
    page.connect_by_mac_clicked()
    qtbot.waitUntil(lambda: not window.runner.busy, timeout=5000)
    assert page.session is None
    assert page.connect_status.text() == "Připojení bylo přerušeno."
    assert window.test_dialogs.errors == []
    removed = window.test_transport.commands[-1]
    assert (removed.path, removed.verb) == ("/ip address", "remove")
    assert removed.selector.get("address") == "192.168.88.253/24"
    assert not window.test_transport.connected


def test_a_plain_connect_cancelled_is_reported_as_cancelled(window, qtbot):
    page = window.device_page

    def discover(transport, on_step):
        page.cancel_connect()  # closes the transport under the task
        transport.execute("/system/resource/print")  # what the next step would do
        raise AssertionError("unreachable")

    def gone(path, /, **args):
        raise ConnectionLost("api-ssl transport is not connected")

    window.test_transport.execute = gone
    page.discover_fn = discover
    connect(window, qtbot)
    assert page.session is None
    assert page.connect_status.text() == "Připojení bylo přerušeno."
    assert window.test_dialogs.errors == []


def test_connect_by_mac_without_local_interface_reports_error(window):
    page = window.device_page
    page.local_interfaces_fn = list
    page.toggle_scan()
    page.scan_tick()
    page.scan_table.selectRow(0)
    window.test_dialogs.answers["question"] = True
    page.connect_by_mac_clicked()
    assert "síťovou kartu" in window.test_dialogs.errors[-1]
    assert window.test_mac_calls == []


def test_connect_by_mac_without_free_address_reports_error(window):
    page = window.device_page
    page.propose_fn = lambda interface, used: None
    page.toggle_scan()
    page.scan_tick()
    page.scan_table.selectRow(0)
    window.test_dialogs.answers["question"] = True
    page.connect_by_mac_clicked()
    assert "volná adresa" in window.test_dialogs.errors[-1]
    assert window.test_mac_calls == []


def test_offline_target_changes_primary_dialect(window):
    page = window.device_page
    page.offline_version.setCurrentText("6.49.10")
    page.offline_ports.setText("ether1,ether2,sfp1")
    page._offline_changed()
    assert window.controller.primary_major == 6
    assert window.controller.ports() == ["ether1", "ether2", "sfp1"]
    page.offline_version.setCurrentText("6.4")  # half typed: ignored
    assert window.controller.primary_major == 6


def test_basic_page_edits_identity_and_wan_type(window, qtbot):
    page = window.basic_page
    page.identity.clear()
    qtbot.keyClicks(page.identity, "PV R1")
    assert window.controller.profile.identity == "PV R1"
    window.controller.recompute()
    assert page.identity.property("fieldError") is True
    page.wan_type.setCurrentIndex(page.wan_type.findData(WanType.PPPOE.value))
    assert window.controller.profile.wan.type == WanType.PPPOE
    assert window.controller.profile.wan.mss_clamping
    page.push_parsed("wan.settings.address", lambda: parse_cidr("nonsense"))
    assert page.has_syntax_errors and window.has_syntax_errors
    assert page.static_address.property("fieldError") is True
    page.push_parsed("wan.settings.address", lambda: parse_cidr("198.51.100.2/24"))
    assert not page.has_syntax_errors


def test_lan_page_locks_wan_and_shows_offload_conflict(window, qtbot):
    connect(window, qtbot)
    window.basic_page._apply_preset()  # re-fill the Router scenario with the device's ports
    window.controller.recompute()
    page = window.lan_page
    wan = window.controller.profile.wan.interface
    locked = next(page.ports.item(i) for i in range(page.ports.count())
                  if page.ports.item(i).data(Qt.ItemDataRole.UserRole) == wan)
    assert not locked.flags() & Qt.ItemFlag.ItemIsEnabled
    assert page.offload_table.rowCount() == 9
    page.protocol.setCurrentIndex(page.protocol.findData(ProtocolMode.MSTP))
    window.controller.recompute()
    assert page.switch_to_rstp.isVisibleTo(page)
    page.switch_to_rstp.click()
    window.controller.recompute()
    assert window.controller.profile.bridge.protocol_mode == ProtocolMode.RSTP
    assert not page.switch_to_rstp.isVisibleTo(page)


def test_enum_combos_store_enums_not_strings(window, qtbot):
    """Qt returns StrEnum item data as a plain str; the check after apply then failed on
    protocol_mode.value with AttributeError (bridge mode, hEX S, 2026-09-23)."""
    page = window.lan_page
    page.protocol.setCurrentIndex(page.protocol.findData(ProtocolMode.NONE))
    page.frame_types.setCurrentIndex(page.frame_types.findData(FrameTypes.ADMIT_ONLY_VLAN_TAGGED))
    page.offload_mode.setCurrentIndex(page.offload_mode.findData(HwOffloadMode.OFF))
    page.addressing.setCurrentIndex(page.addressing.findData(LanAddressing.DHCP_CLIENT))
    bridge, lan = window.controller.profile.bridge, window.controller.profile.lan
    assert type(bridge.protocol_mode) is ProtocolMode and bridge.protocol_mode.value == "none"
    assert type(bridge.frame_types) is FrameTypes
    assert type(bridge.hw_offload) is HwOffloadMode and bridge.hw_offload == HwOffloadMode.OFF
    assert type(lan.addressing) is LanAddressing

    device = window.device_page
    device.offline_ipv6.setCurrentIndex(device.offline_ipv6.findData(Ipv6PackageState.DISABLED))
    assert type(window.controller.offline_ipv6) is Ipv6PackageState
    connect(window, qtbot)
    assert type(window.test_connect_calls[0][1]) is TransportChoice


def test_lan_page_static_address_and_gateway(window):
    page = window.lan_page
    page.addressing.setCurrentIndex(page.addressing.findData(LanAddressing.DHCP_CLIENT))
    assert not page.lan_address.isEnabled() and not page.lan_gateway.isEnabled()
    assert not window.controller.profile.lan.dhcp_server.enabled

    page.addressing.setCurrentIndex(page.addressing.findData(LanAddressing.STATIC))
    assert page.lan_address.isEnabled() and page.lan_gateway.isEnabled()
    assert page.lan_address.text() == "192.168.88.1/24"
    page.push_parsed("lan.gateway", lambda: parse_optional_ip("192.168.88.254"))
    assert str(window.controller.profile.lan.gateway) == "192.168.88.254"
    window.controller.recompute()
    assert "add dst-address=0.0.0.0/0 gateway=192.168.88.254" in \
        window.preview_page.views[7].toPlainText()

    page.addressing.setCurrentIndex(page.addressing.findData(LanAddressing.NONE))
    assert window.controller.profile.lan.gateway is None
    assert page.lan_gateway.text() == ""


def test_lan_page_vlan_filtering_enables_pvid_and_frame_types(window):
    """A page gets no profileChanged for its own edit, so the gating must not live in load()."""
    page = window.lan_page
    assert not page.pvid.isEnabled() and not page.frame_types.isEnabled()

    page.vlan_filtering.setChecked(True)
    assert window.controller.profile.bridge.vlan_filtering
    assert page.pvid.isEnabled() and page.frame_types.isEnabled()

    page.pvid.setValue(10)
    page.frame_types.setCurrentIndex(page.frame_types.findData(FrameTypes.ADMIT_ONLY_VLAN_TAGGED))
    window.controller.recompute()
    assert "vlan-filtering=yes pvid=10 frame-types=admit-only-vlan-tagged" in \
        window.preview_page.views[7].toPlainText()

    page.vlan_filtering.setChecked(False)
    assert not page.pvid.isEnabled() and not page.frame_types.isEnabled()


def test_basic_page_wan_checkbox_gates_the_wan_fields(window):
    page = window.basic_page
    assert page.wan_interface.isEnabled()
    page.wan_enabled.setChecked(False)
    assert not window.controller.profile.wan.enabled
    assert not page.wan_interface.isEnabled() and not page.wan_type.isEnabled()
    page.wan_enabled.setChecked(True)
    assert page.wan_interface.isEnabled() and page.wan_nat.isEnabled()


def test_basic_page_switches_the_wan_into_a_vlan(window, qtbot):
    page = window.basic_page
    assert not page.wan_vlan_enabled.isChecked()
    assert not page.wan_vlan_id.isEnabled() and not page.wan_vlan_name.isEnabled()

    page.wan_vlan_enabled.setChecked(True)  # the Cetin default comes with the checkbox
    assert window.controller.profile.wan.vlan_id == CETIN_VLAN_ID
    assert window.controller.profile.wan.vlan_interface == "vlan848"
    assert page.wan_vlan_id.isEnabled() and page.wan_vlan_name.isEnabled()

    page.wan_vlan_id.setValue(100)
    assert window.controller.profile.wan.vlan_id == 100
    qtbot.keyClicks(page.wan_vlan_name, "cetin")
    assert window.controller.profile.wan.vlan_name == "cetin"

    page.wan_vlan_enabled.setChecked(False)
    assert window.controller.profile.wan.vlan_id is None
    assert window.controller.profile.wan.vlan_interface is None
    page.wan_vlan_id.setValue(848)  # an id typed with the checkbox off changes nothing
    assert window.controller.profile.wan.vlan_id is None

    page.wan_enabled.setChecked(False)
    assert not page.wan_vlan_enabled.isEnabled()


def test_firewall_fasttrack_asks_about_shaping(window):
    page = window.firewall_page
    window.test_dialogs.answers["question"] = True  # uses shaping -> keep FastTrack off
    page.fasttrack.setChecked(True)
    assert not page.fasttrack.isChecked()
    assert not window.controller.profile.firewall.fasttrack.enabled
    assert not page.fasttrack_hw.isEnabled()
    window.test_dialogs.answers["question"] = False
    page.fasttrack.setChecked(True)
    fasttrack = window.controller.profile.firewall.fasttrack
    assert fasttrack.enabled and fasttrack.no_shaping_confirmed
    assert page.fasttrack_hw.isEnabled()


def test_firewall_ipv6_checkbox(window):
    page = window.firewall_page
    assert not page.ipv6.isChecked()
    page.ipv6.setChecked(True)
    assert window.controller.profile.firewall.ipv6
    # The IPv6 input chain opens management from WAN even without the basic filter.
    page.basic_filter.setChecked(False)
    assert page.wan_management.isEnabled() == window.controller.profile.wan.enabled
    page.ipv6.setChecked(False)
    assert not window.controller.profile.firewall.ipv6
    assert not page.wan_management.isEnabled()


def test_remote_dns_follows_basic_filter(window):
    services, firewall = window.services_page, window.firewall_page
    firewall.basic_filter.setChecked(True)
    assert services.dns_remote.isEnabled() and not services.dns_remote.isChecked()
    services.dns_remote.setChecked(True)
    assert window.controller.profile.router_is_dns
    firewall.basic_filter.setChecked(False)
    assert not window.controller.profile.services.dns_remote_requests
    assert not services.dns_remote.isChecked() and not services.dns_remote.isEnabled()
    firewall.basic_filter.setChecked(True)
    assert services.dns_remote.isEnabled() and not services.dns_remote.isChecked()


def test_services_page_two_way_binding_and_reset(window):
    page = window.services_page
    page.ssh_port.setValue(2222)
    assert "set ssh port=2222" in page.editor.toPlainText()
    assert "set ssh port=2222" in window.controller.profile.services.hardening_text
    page.editor.setPlainText(page.editor.toPlainText().replace(
        "set servers=192.168.200.200,8.8.8.8", "set servers=1.1.1.1"))
    assert page.dns.text() == "1.1.1.1"
    window.controller.recompute()
    assert "api-ssl 8729" in page.access.text()
    window.test_dialogs.answers["question"] = True
    page._reset()
    assert window.controller.profile.services.hardening_is_default
    page.subnet_enabled.setChecked(True)
    assert page.has_syntax_errors
    page.subnet.setText("10.0.0.0/24")
    page._subnet_changed()
    assert str(window.controller.profile.services.management_subnet) == "10.0.0.0/24"


def test_preview_copy_and_save(window, tmp_path):
    window.controller.recompute()
    page = window.preview_page
    page.selector.button(6).setChecked(True)
    assert page.views[6].isVisibleTo(page) and not page.views[7].isVisibleTo(page)
    assert "primary-ntp=" in page.views[6].toPlainText()
    page.copy_script()
    assert "primary-ntp=" in QGuiApplication.clipboard().text()
    page.save_script()
    saved = tmp_path / "MikroTik-v6.rsc"
    assert saved.exists() and "primary-ntp=" in saved.read_text(encoding="utf-8")
    page.selector.button(0).setChecked(True)
    assert page.views[6].isVisibleTo(page) and page.views[7].isVisibleTo(page)


def test_apply_generate_only_saves_script(window, tmp_path):
    page = window.apply_page
    page.mode_buttons[ApplyMode.GENERATE_ONLY].setChecked(True)
    assert page.apply_button.isEnabled()
    page.start_apply()
    assert (tmp_path / "MikroTik-v7.rsc").exists()


def test_apply_defaults_to_reset(window):
    assert window.apply_page.mode == ApplyMode.RESET_AND_IMPORT


def test_apply_requires_connection_then_runs_with_rollback(window, qtbot):
    page = window.apply_page
    page.mode_buttons[ApplyMode.IMPORT].setChecked(True)
    window.controller.recompute()
    page.refresh()
    assert not page.apply_button.isEnabled()  # not connected
    connect(window, qtbot)
    page.rollback.setChecked(True)
    page.refresh()
    assert page.apply_button.isEnabled(), [i.message for i in page.preflight_issues()]
    page.start_apply()
    qtbot.waitUntil(lambda: page.last_report is not None and not window.runner.busy,
                    timeout=5000)
    assert window.test_dialogs.dry_runs and "/system identity" in window.test_dialogs.dry_runs[0]
    orchestrator = RecordingOrchestrator.instances[-1]
    assert orchestrator.ctx.options.rollback_minutes == 5
    assert page.checklist.rowCount() == 1 and page.checklist.item(0, 1).text() == "OK"
    assert page.confirm_button.isVisibleTo(page)
    # The rollback is cancelled over this connection, so it stays until confirmed.
    assert window.device_page.session is not None and window.test_transport.connected
    page.confirm_works()
    qtbot.waitUntil(lambda: orchestrator.confirmed and not window.runner.busy, timeout=5000)
    assert window.device_page.session is None and not window.test_transport.connected
    assert page.result_label.text() == "Konfigurace potvrzena, zařízení odpojeno."


def test_a_successful_apply_disconnects(window, qtbot):
    connect(window, qtbot)
    page = window.apply_page
    page.start_apply()
    qtbot.waitUntil(lambda: page.last_report is not None and not window.runner.busy,
                    timeout=5000)
    assert RecordingOrchestrator.instances[-1].ctx.options.mode == ApplyMode.RESET_AND_IMPORT
    assert window.device_page.session is None and not window.test_transport.connected
    assert window.status_connection.text() == "Odpojeno"
    assert page.result_label.text() == "Konfigurace aplikována, zařízení odpojeno."
    assert not page.apply_button.isEnabled()  # not connected any more


def test_failed_checks_keep_the_device_connected(window, qtbot):
    class FailingChecks(RecordingOrchestrator):
        def run(self):
            report = super().run()
            report.checks = [CheckResult("verify.identity", False,
                                         {"expected": "R", "actual": "X"})]
            report.succeeded = False
            return report

    window.apply_page.orchestrator_factory = FailingChecks
    connect(window, qtbot)
    page = window.apply_page
    page.start_apply()
    qtbot.waitUntil(lambda: page.last_report is not None and not window.runner.busy,
                    timeout=5000)
    assert window.device_page.session is not None and window.test_transport.connected
    assert page.result_label.text() == "Konfigurace aplikována, ale některé kontroly selhaly."


def test_a_successful_apply_by_mac_gives_the_lent_address_back(window, qtbot):
    page = connect_by_mac(window, qtbot)
    window.controller.recompute()
    window.apply_page.start_apply()
    qtbot.waitUntil(lambda: window.apply_page.last_report is not None
                    and not window.runner.busy and page.connect_button.isEnabled(),
                    timeout=5000)
    assert page.session is None
    assert removals(window)[0].selector.get("address") == "192.168.88.253/24"
    assert "odebrána" in page.connect_status.text()


def test_apply_cancelled_in_dry_run(window, qtbot):
    connect(window, qtbot)
    window.test_dialogs.answers["dry_run"] = False
    window.apply_page.start_apply()
    qtbot.waitUntil(lambda: bool(window.test_dialogs.dry_runs) and not window.runner.busy,
                    timeout=5000)
    assert RecordingOrchestrator.instances == []


def test_reset_identity_must_match(window, qtbot):
    connect(window, qtbot)
    page = window.apply_page
    page.mode_buttons[ApplyMode.RESET_AND_IMPORT].setChecked(True)
    page.admin_password.setText("N3w")
    window.test_dialogs.answers["identity"] = "wrong"
    page.start_apply()
    qtbot.waitUntil(lambda: bool(window.test_dialogs.errors) and not window.runner.busy,
                    timeout=5000)
    assert RecordingOrchestrator.instances == []


def test_only_the_new_admin_password_is_prefilled(window, qtbot):
    assert window.device_page.password.text() == ""  # a login is never guessed
    page = window.apply_page
    assert page.admin_password.text() == DEFAULT_PASSWORD
    connect(window, qtbot)
    page.mode_buttons[ApplyMode.RESET_AND_IMPORT].setChecked(True)
    assert page.options().admin_password == DEFAULT_PASSWORD
    assert page.apply_button.isEnabled()  # the default is a password, so reset can go ahead


def test_reset_keeping_users_needs_no_admin_password(window, qtbot):
    connect(window, qtbot)
    page = window.apply_page
    page.mode_buttons[ApplyMode.IMPORT].setChecked(True)
    assert not page.keep_users.isEnabled()
    page.mode_buttons[ApplyMode.RESET_AND_IMPORT].setChecked(True)
    page.admin_password.clear()
    assert page.keep_users.isEnabled() and not page.apply_button.isEnabled()  # no password
    page.keep_users.setChecked(True)
    assert page.apply_button.isEnabled() and page.admin_password.placeholderText()
    page.start_apply()
    qtbot.waitUntil(lambda: page.last_report is not None and not window.runner.busy,
                    timeout=5000)
    options = RecordingOrchestrator.instances[-1].ctx.options
    assert options.keep_users and options.admin_password == ""
    assert window.test_dialogs.reset_keep_users
    page.mode_buttons[ApplyMode.IMPORT].setChecked(True)
    assert not page.options().keep_users  # never outside a reset


def arm_plan(directory, inventory):
    """The fixture's device, said to be an arm board like the hEX S E60iUGS."""
    resource = inventory.resource.model_copy(update={"architecture": "arm"})
    return plan_from_dir(directory, inventory.model_copy(update={"resource": resource}))


def test_update_is_on_by_default_and_belongs_to_a_reset(window, qtbot):
    page = window.apply_page
    assert page.update_check.isChecked() and page.update_check.isEnabled()
    assert str(page.update_dir) in page.update_dir_label.text()
    assert page.options().update is None  # no device: its architecture is not known yet
    connect(window, qtbot)
    assert page.options().update is not None
    page.mode_buttons[ApplyMode.IMPORT].setChecked(True)
    assert not page.update_check.isEnabled() and page.options().update is None


def test_routerboot_has_its_own_switch(window, qtbot):
    page = window.apply_page
    connect(window, qtbot)
    assert page.routerboot_check.isChecked() and page.options().upgrade_routerboot
    page.update_check.setChecked(False)
    assert page.options().upgrade_routerboot and page.options().update is None
    page.update_check.setChecked(True)
    page.routerboot_check.setChecked(False)
    assert not page.options().upgrade_routerboot and page.options().update is not None
    page.routerboot_check.setChecked(True)
    page.mode_buttons[ApplyMode.IMPORT].setChecked(True)
    assert not page.routerboot_check.isEnabled() and not page.options().upgrade_routerboot


def test_the_update_is_shown_before_the_apply_and_handed_to_it(window, qtbot, tmp_path):
    (tmp_path / "Update").mkdir()
    (tmp_path / "Update" / "routeros-7.23.7-arm.npk").write_bytes(make_npk())
    (tmp_path / "Update" / "routeros-7.23.7-mmips.npk").write_bytes(make_npk(arch="mmips"))
    page = window.apply_page
    page.plan_update_fn = arm_plan
    connect(window, qtbot)
    page.refresh()
    planned = [i for i in page.preflight_issues() if i.code == "update.planned"]
    assert len(planned) == 1 and "routeros-7.23.7-arm.npk" in planned[0].message
    assert page.apply_button.isEnabled()
    page.start_apply()
    qtbot.waitUntil(lambda: page.last_report is not None and not window.runner.busy,
                    timeout=5000)
    options = RecordingOrchestrator.instances[-1].ctx.options
    assert options.installs_update
    assert [p.file_name for p in options.update.packages] == ["routeros-7.23.7-arm.npk"]


def test_the_update_can_be_switched_off(window, qtbot, tmp_path):
    (tmp_path / "Update").mkdir()
    (tmp_path / "Update" / "routeros-7.23.7-arm.npk").write_bytes(make_npk())
    page = window.apply_page
    page.plan_update_fn = arm_plan
    connect(window, qtbot)
    page.update_check.setChecked(False)
    assert not any(i.code.startswith("update.") for i in page.preflight_issues())
    page.start_apply()
    qtbot.waitUntil(lambda: page.last_report is not None and not window.runner.busy,
                    timeout=5000)
    assert RecordingOrchestrator.instances[-1].ctx.options.update is None


def test_without_a_package_the_reset_goes_ahead_with_a_warning(window, qtbot):
    page = window.apply_page
    connect(window, qtbot)  # no Update directory in this test
    page.refresh()
    issues = [i for i in page.preflight_issues() if i.code.startswith("update.")]
    assert [i.code for i in issues] == ["update.no_dir"]
    assert page.apply_button.isEnabled()


def test_profiles_menu_roundtrip(window, tmp_path):
    window.basic_page.identity.setText("PV-SAVED")
    window.controller.edit(lambda p: p.model_copy(update={"identity": "PV-SAVED"}))
    window.test_dialogs.answers["text"] = "pobocka"
    window.save_profile()
    assert window.store.list() == ["pobocka"]
    window.new_profile()
    assert window.controller.profile.identity == "MikroTik"
    window.test_dialogs.answers["item"] = "pobocka"
    window.open_profile()
    assert window.controller.profile.identity == "PV-SAVED"
    window.export_profile_file()
    exported = tmp_path / "pobocka.json"
    assert exported.exists()
    window.test_dialogs.answers["open"] = exported
    window.import_profile_file()
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    window.test_dialogs.answers["open"] = bad
    window.import_profile_file()
    assert "platný JSON" in window.test_dialogs.errors[-1]
    window.save_profile()  # current name known -> no prompt


def test_close_window_cleans_up(window, qtbot):
    connect(window, qtbot)
    window.close()
    assert not window.test_transport.connected
    window.set_dark(True)


def connect_by_mac(win, qtbot):
    page = win.device_page
    page.toggle_scan()
    page.scan_tick()
    page.scan_table.selectRow(0)
    page.password.setText("pw")
    win.test_dialogs.answers["question"] = True
    page.connect_by_mac_clicked()
    qtbot.waitUntil(lambda: page.session is not None and not win.runner.busy, timeout=5000)
    return page


def removals(win):
    return [c for c in win.test_transport.commands if c.verb == "remove"]


def test_disconnect_gives_the_lent_address_back(window, qtbot):
    page = connect_by_mac(window, qtbot)
    page.disconnect_device()
    # Off the UI thread, and no new connection until it is done.
    assert "odebírám" in page.connect_status.text()
    assert not page.connect_button.isEnabled()
    qtbot.waitUntil(lambda: not window.runner.busy and page.connect_button.isEnabled(),
                    timeout=5000)
    assert removals(window)[0].selector.get("address") == "192.168.88.253/24"
    assert "odebrána" in page.connect_status.text()
    assert not window.test_transport.connected
    assert window.status_connection.text() == "Odpojeno"


def test_an_address_left_behind_is_reported(window, qtbot):
    page = connect_by_mac(window, qtbot)
    window.test_transport.close()  # the connection died meanwhile
    page.disconnect_device()
    qtbot.waitUntil(lambda: not window.runner.busy and page.connect_button.isEnabled(),
                    timeout=5000)
    assert "zůstala" in page.connect_status.text()
    assert "192.168.88.253/24" in page.connect_status.text()


def test_after_a_reset_there_is_no_lent_address_to_give_back(window, qtbot):
    page = connect_by_mac(window, qtbot)
    new = FakeTransport({})
    new.connect()
    window.apply_page.applied.emit(ApplyReport(names=make_names(datetime(2026, 9, 21), False),
                                               transport=new, temp_address=None))
    assert page.session.transport is new and page.session.temp_address is None
    assert "mqs-temp" not in page.connect_status.text()
    page.disconnect_device()
    assert page.connect_status.text() == "Odpojeno"
    assert removals(window) == [] and not new.connected


def test_back_in_by_mac_after_an_apply_the_new_lent_address_is_given_back(window, qtbot):
    page = connect_by_mac(window, qtbot)
    new = FakeTransport({})
    new.params = ConnectionParams(host="192.168.88.253", username="admin", password="N3w")
    new.connect()
    window.apply_page.applied.emit(ApplyReport(
        names=make_names(datetime(2026, 9, 21), False), transport=new,
        temp_address="192.168.88.253/24", mac_method="mac-telnet"))
    assert page.connect_status.text().startswith("Připojeno: 01:02:03:04:05:06")
    assert window.status_connection.text().startswith("Připojeno: 01:02:03:04:05:06")
    page.disconnect_device()
    qtbot.waitUntil(lambda: not window.runner.busy and page.connect_button.isEnabled(),
                    timeout=5000)
    removed = [c for c in new.commands if c.verb == "remove"]
    assert removed[0].selector.get("address") == "192.168.88.253/24"


def test_back_in_by_address_after_an_apply_the_address_is_shown(window, qtbot):
    page = connect_by_mac(window, qtbot)
    new = FakeTransport({})
    new.params = ConnectionParams(host="192.168.88.1", username="admin")
    window.apply_page.applied.emit(ApplyReport(names=make_names(datetime(2026, 9, 21), False),
                                               transport=new, temp_address=None))
    assert page.connect_status.text().startswith("Připojeno: 192.168.88.1 (api-ssl)")
    assert page.session.mac_route is not None, "the way back by MAC is still known"


def test_closing_the_window_gives_the_lent_address_back(window, qtbot):
    connect_by_mac(window, qtbot)
    window.close()
    assert removals(window) and not window.test_transport.connected


def test_ports_are_renamed_on_the_basic_page(window):
    page, table = window.basic_page, window.basic_page.port_names
    assert table.rowCount() == 5 and table.item(0, 0).text() == "ether1"
    table.item(0, 1).setText("internet")  # ether1, the WAN port of the router preset
    profile = window.controller.profile
    assert profile.interface_names == {"ether1": "internet"}
    assert profile.wan.interface == "internet"
    assert page.wan_interface.currentData() == "internet"
    lan = window.lan_page.ports
    assert lan.item(0).text() == "internet (ether1) – WAN port"
    assert window.controller.recompute().primary.text.count(
        "set [ find default-name=ether1 ] name=internet") == 1

    # A name another port has is refused, and the cell shows the name it keeps.
    table.item(1, 1).setText("internet")
    assert page.port_name_error.isVisibleTo(page) and "ether1" in page.port_name_error.text()
    assert table.item(1, 1).text() == "ether2"
    table.item(1, 1).setText("  ")
    assert "prázdný" in page.port_name_error.text()
    assert window.controller.profile.interface_names == {"ether1": "internet"}
    table.item(1, 1).setText(" lan2 ")
    assert not page.port_name_error.isVisibleTo(page)
    assert window.controller.profile.bridge.ports[0] == "lan2"
    assert table.item(1, 1).text() == "lan2"


def test_a_renamed_device_shows_its_names_everywhere(window, qtbot):
    names = {"ether1-uplink": "ether1", "ether2-zakaznik1": "ether2"}
    ports = [*names, *TEN_PORTS[2:]]
    inventory = make_inventory("7.20.7", ports, default_names=names)
    window.device_page.discover_fn = lambda transport, on_step: inventory
    connect(window, qtbot)
    profile = window.controller.profile
    # The router preset of the start named the factory ports; now they follow the device.
    assert profile.wan.interface == "ether1-uplink"
    assert profile.bridge.ports[0] == "ether2-zakaznik1"
    assert profile.interface_names == {"ether1": "ether1-uplink", "ether2": "ether2-zakaznik1"}
    device = window.device_page.ports_table
    assert (device.item(0, 0).text(), device.item(0, 1).text()) == ("ether1-uplink", "ether1")
    table = window.basic_page.port_names
    assert [table.item(0, c).text() for c in range(3)] == ["ether1", "ether1-uplink",
                                                           "ether1-uplink"]
    assert window.lan_page.ports.item(1).text() == "ether2-zakaznik1 (ether2)"
    analysis = window.controller.recompute()
    assert not {"bridge.port_unknown", "wan.interface_unknown"} & {
        i.code for i in analysis.all_issues}


# ---------------------------------------------------------------- another RouterOS version


V6_MEMBERS = ("system", "ipv6", "wireless", "hotspot", "mpls", "routing", "ppp", "dhcp",
              "security", "advanced-tools")


def mmips_inventory(version="6.49.21"):
    """The v6 hEX S: mmips, a RAM-disk file root, and factory-firmware 6.42.4."""
    return make_inventory(version, TEN_PORTS[:5], architecture="mmips", has_flash_dir=True,
                          free_memory=227 * 1024 * 1024, free_hdd_space=4636 * 1024,
                          factory_firmware="6.42.4", factory_software="6.42.4",
                          board_name="hEX S",
                          packages=[PackageInfo(name="routeros-mmips", version=version),
                                    *(PackageInfo(name=m, version=version,
                                                  bundle="routeros-mmips")
                                      for m in V6_MEMBERS)])


def mmips_packages(tmp_path, *versions):
    folder = tmp_path / "Update"
    folder.mkdir(exist_ok=True)
    for version in versions or ((6, 40, 9), (6, 49, 12), (6, 49, 21),
                                (6, 49, 22), (7, 23, 5)):
        dotted = ".".join(str(part) for part in version)
        if version[0] == 6:
            (folder / f"routeros-mmips-{dotted}.npk").write_bytes(
                make_npk("routeros-mmips", version, "mmips", bundle=True))
        else:
            (folder / f"routeros-{dotted}-mmips.npk").write_bytes(
                make_npk("system", version, "mmips"))
    return folder


class RecordingChanger:
    instances: ClassVar[list[RecordingChanger]] = []

    def __init__(self, ctx):
        self.ctx = ctx
        RecordingChanger.instances.append(self)

    def run(self):
        plan = self.ctx.plan
        report = VersionReport(
            transport=self.ctx.transport,
            installed=[hop.version for hop in plan.hops],
            checks=[CheckResult("verify.routeros_version", True,
                                {"expected": str(plan.target), "actual": str(plan.target)})])
        report.succeeded = True
        return report


def v6_window(window, qtbot, tmp_path, version="6.49.21"):
    """A window connected to the v6 hEX S, with the packages in the Update directory."""
    mmips_packages(tmp_path)
    window.device_page.discover_fn = lambda transport, on_step: mmips_inventory(version)
    page = window.apply_page
    page.changer_factory = RecordingChanger
    RecordingChanger.instances.clear()
    connect(window, qtbot)
    return page


def test_the_versions_on_offer_include_the_ones_out_of_reach(window, qtbot, tmp_path):
    page = v6_window(window, qtbot, tmp_path)
    page.version_check.setChecked(True)
    labels = [page.version_combo.itemText(i) for i in range(page.version_combo.count())]
    assert [label.split()[0] for label in labels] == ["7.23.5", "6.49.22", "6.49.21",
                                                      "6.49.12", "6.40.9"]
    assert "verze na zařízení" in labels[2]  # 6.49.21 runs now
    assert "nelze" in labels[4]  # 6.40.9 is below the factory firmware
    blocked = page.version_combo.itemData(4, Qt.ItemDataRole.ToolTipRole)
    assert "6.42.4" in blocked
    assert page.chosen_version == "7.23.5"  # the first one it can have
    assert "6.42.4" in page.version_note.text()


def test_a_version_below_the_factory_one_cannot_be_started(window, qtbot, tmp_path):
    page = v6_window(window, qtbot, tmp_path)
    page.version_check.setChecked(True)
    page.version_combo.setCurrentIndex(4)  # 6.40.9
    assert not page.version_button.isEnabled()
    assert "version.below_factory" not in page.version_note.text()  # the message, not the code
    assert "6.42.4" in page.version_note.text()


def test_the_update_and_the_version_change_are_exclusive(window, qtbot, tmp_path):
    page = v6_window(window, qtbot, tmp_path)
    assert page.update_check.isChecked()
    page.version_check.setChecked(True)
    assert not page.update_check.isChecked() and page.options().update is None
    page.update_check.setChecked(True)
    assert not page.version_check.isChecked()


def test_a_downgrade_from_the_window(window, qtbot, tmp_path):
    page = v6_window(window, qtbot, tmp_path)
    page.version_check.setChecked(True)
    page.version_combo.setCurrentIndex(3)  # 6.49.12, one version back
    assert page.version_button.isEnabled()
    reports = []
    page.version_changed.connect(reports.append)
    page.start_version_change()
    qtbot.waitUntil(lambda: bool(reports) and not window.runner.busy, timeout=5000)
    plan = RecordingChanger.instances[-1].ctx.plan
    assert str(plan.target) == "6.49.12" and plan.reboots == 1
    assert [str(v) for v in reports[0].installed] == ["6.49.12"]
    # Confirmed by the device's own name, as a reset is.
    assert window.test_dialogs.version_asked["version"] == "6.49.12"
    assert not page.version_check.isChecked()  # done: the switch goes back off
    assert "6.49.12" in page.result_label.text()


def test_the_version_change_takes_the_backup_the_apply_options_ask_for(window, qtbot,
                                                                       tmp_path):
    """"Stáhnout zálohu" and the number of backups kept are the apply's own switches, and
    the version change reads them the same way: it is the same safety net."""
    page = v6_window(window, qtbot, tmp_path)
    page.download.setChecked(True)
    page.download_dir.setText(str(tmp_path / "Zálohy"))
    page.keep_backups.setValue(5)
    page.version_check.setChecked(True)
    page.version_combo.setCurrentIndex(3)  # 6.49.12
    page.start_version_change()
    qtbot.waitUntil(lambda: bool(RecordingChanger.instances) and not window.runner.busy,
                    timeout=5000)
    ctx = RecordingChanger.instances[-1].ctx
    assert ctx.download_dir == tmp_path / "Zálohy" and ctx.keep_backups == 5
    page.download.setChecked(False)
    page.version_check.setChecked(True)
    page.version_combo.setCurrentIndex(3)
    page.start_version_change()
    qtbot.waitUntil(lambda: not window.runner.busy, timeout=5000)
    assert RecordingChanger.instances[-1].ctx.download_dir is None  # device only


def test_a_downgrade_the_user_does_not_confirm_is_not_run(window, qtbot, tmp_path):
    page = v6_window(window, qtbot, tmp_path)
    window.test_dialogs.answers["identity"] = None  # Cancel in the dialog
    page.version_check.setChecked(True)
    page.version_combo.setCurrentIndex(3)
    page.start_version_change()
    assert not RecordingChanger.instances


def test_an_upgrade_inside_the_major_version_asks_a_plain_question(window, qtbot, tmp_path):
    page = v6_window(window, qtbot, tmp_path, version="6.49.12")
    window.test_dialogs.answers["question"] = False
    page.version_check.setChecked(True)
    page.version_combo.setCurrentIndex(2)  # 6.49.21: newer, inside v6
    plan = page.version_plan()
    assert not plan.needs_confirmation
    page.start_version_change()
    assert not RecordingChanger.instances  # the question was answered with no
    window.test_dialogs.answers["question"] = True
    page.start_version_change()
    qtbot.waitUntil(lambda: bool(RecordingChanger.instances) and not window.runner.busy,
                    timeout=5000)
    assert str(RecordingChanger.instances[-1].ctx.plan.target) == "6.49.21"


# ------------------------------------------------------------- the window's own command line


def parsed(*argv):
    args, rest = parse_args(argv)
    return args, rest


def test_the_command_line_fills_the_connect_form(window, qtbot):
    args, rest = parsed("--host", "192.168.55.129", "--user", "bee", "--key", "k.pem",
                        "--transport", "ssh", "--ssh-port", "122", "--connect")
    page = window.device_page
    assert prefill(page, args) is True
    assert rest == []
    assert (page.host.text(), page.user.text(), page.key_file.text()) == (
        "192.168.55.129", "bee", "k.pem")
    assert page.ssh_port.value() == 122
    assert page.transport_choice.currentData() == TransportChoice.SSH
    # Untouched: the page keeps what it chose itself.
    assert page.password.text() == ""
    assert page.api_port.value() == 8729


def test_without_connect_or_a_host_nothing_is_connected(window, qtbot):
    args, _ = parsed("--host", "192.168.55.129")
    assert prefill(window.device_page, args) is False
    args, _ = parsed("--connect")  # no address to connect to
    assert prefill(window.device_page, args) is False


def test_the_password_comes_from_the_environment_only(window, qtbot):
    page = window.device_page
    args, _ = parsed("--password-env", "MQS_TEST_PASSWORD")
    prefill(page, args, env={"MQS_TEST_PASSWORD": "s3cret"})
    assert page.password.text() == "s3cret"
    prefill(page, args, env={"MQS_TEST_PASSWORD": ""})  # a key-only run says "none"
    assert page.password.text() == ""
    page.password.setText("typed by hand")
    prefill(page, args, env={})  # the variable is not there: leave the field be
    assert page.password.text() == "typed by hand"


def test_qt_keeps_its_own_arguments(window, qtbot):
    args, rest = parsed("--host", "10.0.0.1", "-style", "Fusion")
    assert rest == ["-style", "Fusion"]
    assert args.host == "10.0.0.1"


def test_the_routerboot_switch_belongs_to_the_version_row_as_well(window, qtbot, tmp_path):
    page = v6_window(window, qtbot, tmp_path)
    page.mode_buttons[ApplyMode.IMPORT].setChecked(True)
    assert not page.routerboot_check.isEnabled()  # nothing installs a version here
    page.version_check.setChecked(True)
    assert page.routerboot_check.isEnabled()
    page.version_combo.setCurrentIndex(0)  # 7.23.5, forward across the major version
    page.start_version_change()
    qtbot.waitUntil(lambda: bool(RecordingChanger.instances) and not window.runner.busy,
                    timeout=5000)
    assert RecordingChanger.instances[-1].ctx.upgrade_routerboot

    RecordingChanger.instances.clear()
    page.version_check.setChecked(True)
    page.routerboot_check.setChecked(False)
    page.version_combo.setCurrentIndex(0)
    page.start_version_change()
    qtbot.waitUntil(lambda: bool(RecordingChanger.instances) and not window.runner.busy,
                    timeout=5000)
    assert not RecordingChanger.instances[-1].ctx.upgrade_routerboot


def test_lowering_the_bootloader_is_a_switch_of_its_own(window, qtbot, tmp_path):
    page = v6_window(window, qtbot, tmp_path)
    assert page.routerboot_check.isChecked()  # on by default, as it always was
    assert not page.routerboot_down_check.isChecked()  # this one never is
    page.routerboot_check.setChecked(False)
    assert not page.routerboot_down_check.isEnabled()  # nothing to modify
    page.routerboot_check.setChecked(True)
    assert page.routerboot_down_check.isEnabled()
    assert not page.options().routerboot_downgrade
    page.routerboot_down_check.setChecked(True)
    assert page.options().routerboot_downgrade

    page.version_check.setChecked(True)
    page.version_combo.setCurrentIndex(3)  # 6.49.12, a downgrade
    page.start_version_change()
    qtbot.waitUntil(lambda: bool(RecordingChanger.instances) and not window.runner.busy,
                    timeout=5000)
    ctx = RecordingChanger.instances[-1].ctx
    assert ctx.upgrade_routerboot and ctx.routerboot_downgrade


def test_the_form_carries_the_key_passphrase(window, qtbot):
    page = window.device_page
    page.host.setText("192.168.55.68")
    page.key_file.setText("C:/keys/id_ed25519")
    page.key_passphrase.setText("heslo klice")
    params = page.connection_params()
    assert (params.key_filename, params.key_passphrase) == ("C:/keys/id_ed25519", "heslo klice")
    # Never on screen, and never in the log: a secret like the others.
    assert page.key_passphrase.echoMode() == QLineEdit.EchoMode.Password
    assert "heslo klice" not in repr(params)
    page.connect_device()
    assert "heslo klice" in list(page.registry)


@pytest.mark.parametrize(("wrong", "expected"), [
    (False, S.CONNECT_KEY_PASSPHRASE),
    (True, S.CONNECT_KEY_PASSPHRASE_WRONG),
])
def test_a_key_passphrase_problem_is_named_as_one(wrong, expected):
    """Not "zkontrolujte uživatele a heslo": the device never saw the login."""
    error = KeyPassphraseError("C:/keys/id_ed25519", wrong=wrong)
    assert describe_error(error) == expected


def test_the_command_line_can_point_at_a_key_passphrase_variable(window, qtbot):
    args, _ = parsed("--host", "h", "--key", "k.pem", "--key-passphrase-env", "MQS_KP")
    page = window.device_page
    prefill(page, args, env={"MQS_KP": "heslo klice"})
    assert page.key_passphrase.text() == "heslo klice"


# ------------------------------------------------------------- remembered between runs


def bare_window(qtbot, tmp_path, store: SettingsStore) -> MainWindow:
    win = MainWindow(dialogs=FakeDialogs(tmp_path), store=ProfileStore(tmp_path / "profiles"),
                     settings_store=store,
                     network_kwargs={"inventory_path": tmp_path / "inventory.json"},
                     apply_kwargs={"update_dir": tmp_path / "Update"})
    qtbot.addWidget(win)
    return win


def fill_login(page) -> None:
    page.host.setText("192.168.55.129")
    page.user.setText("bee")
    page.password.setText("s3cret")
    page.key_file.setText("k.pem")
    page.key_passphrase.setText("phrase")
    page.ca_file.setText("ca.pem")
    page.transport_choice.setCurrentIndex(
        page.transport_choice.findData(TransportChoice.SSH))
    page.api_port.setValue(8730)
    page.ssh_port.setValue(122)


def test_a_remembered_login_comes_back_after_a_restart(qtbot, tmp_path):
    store = settings_store(tmp_path)
    first = bare_window(qtbot, tmp_path, store)
    assert not first.device_page.remember_login.isChecked()  # off until asked for
    fill_login(first.device_page)
    first.device_page.remember_login.setChecked(True)
    first.close()
    assert b"s3cret" not in (tmp_path / "settings" / "login.bin").read_bytes()

    page = bare_window(qtbot, tmp_path, settings_store(tmp_path)).device_page
    assert page.remember_login.isChecked()
    assert page.saved_login() == SavedLogin(
        host="192.168.55.129", username="bee", password="s3cret", key_file="k.pem",
        key_passphrase="phrase", transport="ssh", api_port=8730, ssh_port=122,
        ca_file="ca.pem")


def test_the_form_is_saved_as_it_is_left(qtbot, tmp_path):
    first = bare_window(qtbot, tmp_path, settings_store(tmp_path))
    first.device_page.remember_login.setChecked(True)
    first.device_page.host.setText("10.0.0.1")  # typed after the box was ticked
    first.close()
    second = bare_window(qtbot, tmp_path, settings_store(tmp_path))
    assert second.device_page.host.text() == "10.0.0.1"


def test_unticking_forgets_the_login(qtbot, tmp_path):
    first = bare_window(qtbot, tmp_path, settings_store(tmp_path))
    fill_login(first.device_page)
    first.device_page.remember_login.setChecked(True)
    assert (tmp_path / "settings" / "login.bin").exists()
    first.device_page.remember_login.setChecked(False)
    assert not (tmp_path / "settings" / "login.bin").exists()
    first.close()
    page = bare_window(qtbot, tmp_path, settings_store(tmp_path)).device_page
    assert not page.remember_login.isChecked()
    assert (page.host.text(), page.password.text()) == ("", "")


def test_with_nowhere_to_keep_it_the_login_cannot_be_remembered(qtbot, tmp_path):
    page = bare_window(qtbot, tmp_path, SettingsStore(None)).device_page
    assert not page.remember_login.isEnabled()


def test_show_passwords_switches_every_password_field(qtbot, tmp_path):
    win = bare_window(qtbot, tmp_path, settings_store(tmp_path))
    secret = [win.device_page.password, win.device_page.key_passphrase,
              win.apply_page.admin_password, win.basic_page.pppoe_password]
    plain = win.device_page.host
    assert all(f.echoMode() == QLineEdit.EchoMode.Password for f in secret)
    win.show_passwords_action.setChecked(True)
    assert all(f.echoMode() == QLineEdit.EchoMode.Normal for f in secret)
    assert plain.echoMode() == QLineEdit.EchoMode.Normal
    win.close()
    # The switch itself is remembered, even without remembering the login.
    again = bare_window(qtbot, tmp_path, settings_store(tmp_path))
    assert again.show_passwords_action.isChecked()
    assert again.device_page.password.echoMode() == QLineEdit.EchoMode.Normal
    again.show_passwords_action.setChecked(False)
    assert again.device_page.password.echoMode() == QLineEdit.EchoMode.Password


def eye(field):
    return next(a for a in field.actions() if a.objectName() == "show-password")


def test_the_eye_shows_one_password_field(qtbot, tmp_path):
    win = bare_window(qtbot, tmp_path, settings_store(tmp_path))
    password, passphrase = win.device_page.password, win.device_page.key_passphrase
    assert eye(password).toolTip() == S.PASSWORD_SHOW
    eye(password).trigger()
    assert password.echoMode() == QLineEdit.EchoMode.Normal
    assert eye(password).toolTip() == S.PASSWORD_HIDE
    assert passphrase.echoMode() == QLineEdit.EchoMode.Password  # only the one clicked
    assert not win.show_passwords_action.isChecked()
    eye(password).trigger()
    assert password.echoMode() == QLineEdit.EchoMode.Password


def test_the_menu_switch_moves_every_eye(qtbot, tmp_path):
    win = bare_window(qtbot, tmp_path, settings_store(tmp_path))
    eye(win.device_page.password).trigger()  # one already shown by hand
    win.show_passwords_action.setChecked(True)
    assert eye(win.device_page.key_passphrase).isChecked()
    win.show_passwords_action.setChecked(False)
    for field in (win.device_page.password, win.device_page.key_passphrase):
        assert field.echoMode() == QLineEdit.EchoMode.Password
        assert not eye(field).isChecked()
