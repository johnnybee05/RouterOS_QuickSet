"""Page and main window smoke tests with fake dialogs, transports and orchestrator."""

from __future__ import annotations

from datetime import datetime
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import ClassVar

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from factories import make_inventory
from fake_transport import FakeTransport
from mqs.apply.orchestrator import ApplyReport
from mqs.apply.plan import ApplyMode, make_names
from mqs.apply.preflight import ExistingObjects
from mqs.apply.verify import CheckResult
from mqs.model.bridge import FrameTypes, HwOffloadMode, LanAddressing, ProtocolMode
from mqs.model.inventory import Ipv6PackageState
from mqs.model.wan import WanType
from mqs.profiles.store import ProfileStore
from mqs.transport.base import AuthError, ConnectionParams
from mqs.transport.factory import ConnectOutcome, TransportChoice
from mqs.transport.mac_connect import MacConnectMethod, MacConnectResult
from mqs.transport.mndp import TlvType, decode, device_from_packet, encode
from mqs.transport.netifs import LocalInterface
from mqs.ui.main_window import MainWindow
from mqs.ui.pages.base import parse_cidr, parse_optional_ip

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
    assert window.nav.count() == 7 and window.stack.count() == 7
    window.nav.setCurrentRow(5)
    assert window.stack.currentWidget() is window.preview_page
    assert window.dialect_badge.text().startswith("Offline")
    assert window.controller.profile.mode.value == "router"


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


def test_apply_requires_connection_then_runs_with_rollback(window, qtbot):
    page = window.apply_page
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
    page.confirm_works()
    qtbot.waitUntil(lambda: orchestrator.confirmed and not window.runner.busy, timeout=5000)


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


def test_reset_keeping_users_needs_no_admin_password(window, qtbot):
    connect(window, qtbot)
    page = window.apply_page
    assert not page.keep_users.isEnabled()  # import mode
    page.mode_buttons[ApplyMode.RESET_AND_IMPORT].setChecked(True)
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
