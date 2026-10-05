from fake_transport import FakeTransport
from mqs.capabilities.matrix import Capabilities
from mqs.capabilities.offload import plan_offload
from mqs.model.bridge import BridgeConfig
from mqs.model.inventory import (
    Ipv6PackageState,
    WirelessMenu,
    WirelessRole,
    WirelessStack,
)
from mqs.model.version import RosVersion
from mqs.transport.base import TransportKind
from mqs.transport.discovery import (
    collect_inventory,
    detect_wireless,
    probe_features,
    role_of,
    session_interface,
    to_bool,
    to_bytes,
    to_optional_bool,
)


def rb4011_v7_api() -> dict:
    """API-style replies (booleans already normalised to yes/no by the transport)."""
    ports = [f"ether{i}" for i in range(1, 11)]
    return {
        "/system/resource/print": [{
            "version": "7.15.3 (stable)", "board-name": "RB4011iGS+",
            "architecture-name": "arm", "uptime": "3d2h", "free-memory": "812345344",
            "total-memory": "1073741824", "cpu": "ARMv7", "factory-software": "6.44",
        }],
        "/system/identity/print": [{"name": "MikroTik"}],
        "/system/routerboard/print": [{"routerboard": "yes", "model": "RB4011iGS+",
                                       "serial-number": "ABC", "current-firmware": "7.15.3",
                                       "factory-firmware": "6.44.6",
                                       "upgrade-firmware": "7.15.3"}],
        "/interface/print": [
            *({"name": p, "type": "ether", "default-name": p, "running": "yes",
               "disabled": "no", "mac-address": f"AA:00:00:00:00:{i:02X}"}
              for i, p in enumerate(ports)),
            {"name": "sfp-sfpplus1", "type": "ether", "default-name": "sfp-sfpplus1",
             "running": "no", "disabled": "no"},
        ],
        "/interface/ethernet/print": [{"name": p, "default-name": p} for p in ports],
        "/interface/ethernet/switch/print": [{"name": "switch1", "type": "RTL8367"},
                                             {"name": "switch2", "type": "RTL8367"}],
        "/interface/ethernet/switch/port/print": [
            *({"name": f"ether{i}", "switch": "switch1"} for i in range(1, 6)),
            *({"name": f"ether{i}", "switch": "switch2"} for i in range(6, 11)),
            {"name": "switch1-cpu", "switch": "switch1"},
        ],
        "/system/package/print": [
            {"name": "routeros", "version": "7.15.3", "disabled": "no", "available": "false"},
            # 7.23 lists what it could download next to what is installed.
            {"name": "container", "version": "", "disabled": "true", "available": "true"},
        ],
        "/ip/service/print": [
            {"name": "ssh", "port": "22", "disabled": "no", "address": "", "certificate": "none"},
            {"name": "api-ssl", "port": "8729", "disabled": "no", "certificate": "api-cert"},
        ],
        "/certificate/print": [{"name": "api-cert"}],
        "/file/print": [{"name": "skins", "type": "directory"}],
        "/interface/bridge/port/print": [
            {"interface": "ether2", "bridge": "bridge", "hw": "yes", "hw-offload": "yes",
             "disabled": "no"},
        ],
        "/console/inspect": [{"name": "chain"}, {"name": "hw-offload"}],
    }


def test_collect_inventory_v7_api():
    steps: list[str] = []
    inv = collect_inventory(FakeTransport(rb4011_v7_api()), on_step=steps.append)
    assert steps[0] == "resource" and steps[-1] == "bridge"
    assert inv.version == RosVersion.parse("7.15.3")
    assert inv.resource.board_name == "RB4011iGS+"
    assert inv.resource.free_memory == 812345344
    assert inv.routerboard.routerboard and inv.routerboard.model == "RB4011iGS+"
    # The floor of a downgrade: the firmware field wins, it carries the patch number.
    assert inv.routerboard.factory_firmware == "6.44.6"
    assert inv.resource.factory_software == "6.44"
    assert inv.version_floor == RosVersion.parse("6.44.6")
    assert inv.interface("ether7").switch == "switch2"
    assert inv.interface("sfp-sfpplus1").switch is None
    assert inv.interface("ether1").running
    assert [s.type for s in inv.switches] == ["RTL8367", "RTL8367"]
    assert inv.wireless == WirelessStack.NONE
    assert inv.ipv6_package_state == Ipv6PackageState.BUILTIN
    assert [p.name for p in inv.packages if p.installed] == ["routeros"]
    assert next(p for p in inv.packages if p.name == "container").available
    ssh = next(s for s in inv.services if s.name == "ssh")
    assert ssh.port == 22 and ssh.certificate == ""
    assert next(s for s in inv.services if s.name == "api-ssl").certificate == "api-cert"
    assert inv.certificates == ["api-cert"]
    assert not inv.has_flash_dir
    assert inv.bridge_ports[0].hw_offload is True

    plan = plan_offload(BridgeConfig(ports=[f"ether{i}" for i in range(2, 11)]),
                        Capabilities.from_inventory(inv))
    assert len(plan.groups) == 2


def hap_v6_ssh() -> dict:
    """SSH-style replies from `print terse`: flags instead of some booleans, units in sizes."""
    return {
        "/system/resource/print": [{"version": "6.40.9 (bugfix)", "board-name": "hAP ac",
                                    "free-memory": "85.3MiB", "total-memory": "128.0MiB",
                                    "free-hdd-space": "4812.0KiB",
                                    "total-hdd-space": "16.3MiB"}],
        "/system/identity/print": [{"name": "old-hap"}],
        "/system/routerboard/print": [{"routerboard": "yes", "model": "RB962UiGS-5HacT2HnT"}],
        "/interface/print": [
            {"_index": "0", "_flags": "R", "name": "ether1", "type": "ether"},
            {"_index": "1", "_flags": "X", "name": "wlan1", "type": "wlan"},
            {"_index": "2", "_flags": "", "type": "ether"},  # malformed, skipped
        ],
        "/interface/ethernet/print": [{"name": "ether1", "default-name": "ether1"}],
        "/interface/ethernet/switch/print": [{"name": "switch1", "type": "Atheros8327"}],
        "/interface/ethernet/switch/port/print": [{"name": "ether1", "switch": "switch1"}],
        "/interface/wireless/print": [{"name": "wlan1"}],
        # As the RB760iGS prints it: the bundle, then every package inside it.
        "/system/package/print": [
            {"_flags": "", "name": "routeros-mipsbe", "version": "6.40.9"},
            {"_flags": "", "name": "system", "version": "6.40.9", "bundle": "routeros-mipsbe"},
            {"_flags": "X", "name": "ipv6", "version": "6.40.9", "bundle": "routeros-mipsbe"},
        ],
        "/ip/service/print": [{"_flags": "X", "name": "telnet", "port": "23"},
                              {"_flags": "", "name": "ssh", "port": "22"}],
        "/file/print": [{"name": "flash", "type": "disk"}],
        "/interface/bridge/port/print": [{"_flags": "H", "interface": "ether2", "bridge": "b"}],
    }


def test_collect_inventory_v6_ssh():
    inv = collect_inventory(FakeTransport(hap_v6_ssh(), kind=TransportKind.SSH))
    assert inv.version.major == 6
    assert inv.resource.free_memory == int(85.3 * 1024**2)
    assert inv.interface("wlan1").disabled and not inv.interface("wlan1").running
    assert inv.interface("ether1").running
    assert len(inv.interfaces) == 2
    assert inv.wireless == WirelessStack.WIRELESS
    assert inv.ipv6_package_state == Ipv6PackageState.DISABLED
    assert next(p for p in inv.packages if p.name == "ipv6").bundle == "routeros-mipsbe"
    assert not next(p for p in inv.packages if p.name == "routeros-mipsbe").bundle
    assert inv.resource.free_hdd_space == int(4812.0 * 1024)
    assert next(s for s in inv.services if s.name == "telnet").disabled
    assert inv.certificates == []  # /certificate menu missing in the fake -> tolerated
    assert inv.has_flash_dir
    assert inv.bridge_ports[0].hw_offload is True and inv.bridge_ports[0].hw is None


def test_chr_without_routerboard_and_switch():
    replies = {k: v for k, v in rb4011_v7_api().items()
               if not k.startswith(("/system/routerboard", "/interface/ethernet/switch"))}
    replies["/interface/wifi/print"] = []
    inv = collect_inventory(FakeTransport(replies))
    assert inv.routerboard is None
    assert inv.switches == [] and inv.interface("ether1").switch is None
    assert inv.wireless == WirelessStack.WIFI


def test_packages_offered_for_download_over_ssh_carry_the_a_flag():
    # hEX S 7.23.5 over SSH, 2026-09-27: the installed package alone has a version.
    replies = dict(rb4011_v7_api())
    replies["/system/package/print"] = [
        {"_index": "0", "_flags": "", "name": "routeros", "version": "7.23.5"},
        {"_index": "1", "_flags": "XA", "name": "wireless", "version": ""},
    ]
    inv = collect_inventory(FakeTransport(replies, kind=TransportKind.SSH))
    wireless = next(p for p in inv.packages if p.name == "wireless")
    assert wireless.available and wireless.disabled and not wireless.installed
    assert next(p for p in inv.packages if p.name == "routeros").installed


def test_detect_wireless_v7_legacy_package():
    fake = FakeTransport({"/interface/wireless/print": []})
    assert detect_wireless(fake, RosVersion.parse("7.12")) == WirelessStack.WIRELESS
    assert detect_wireless(FakeTransport({}), RosVersion.parse("6.49")) == WirelessStack.NONE


def test_probe_features():
    fake = FakeTransport(rb4011_v7_api())
    assert probe_features(fake, RosVersion.parse("7.15.3")) == {"fasttrack_hw_offload": True}
    assert fake.calls[-1] == ("/console/inspect", {"request": "child",
                                                   "path": "ip,firewall,filter,add"})
    assert probe_features(FakeTransport({}), RosVersion.parse("7.15.3")) == {
        "fasttrack_hw_offload": False}
    assert probe_features(fake, RosVersion.parse("6.49")) == {"fasttrack_hw_offload": False}


def test_session_interface():
    replies = {
        "/ip/arp/print": [{"address": "192.168.88.254", "mac-address": "11:22:33:44:55:66",
                           "interface": "bridge"},
                          {"address": "10.0.0.2", "mac-address": "AA", "interface": "ether1"}],
        "/interface/bridge/host/print": [{"mac-address": "11:22:33:44:55:66", "bridge": "bridge",
                                          "on-interface": "ether3"}],
    }
    fake = FakeTransport(replies)
    assert session_interface(fake, "192.168.88.254") == "ether3"
    assert session_interface(fake, "10.0.0.2") == "ether1"
    assert session_interface(fake, "10.9.9.9") is None
    assert session_interface(FakeTransport({}), "10.0.0.2") is None


def test_value_helpers():
    assert to_bool({"disabled": "true"}, "disabled")
    assert to_bool({"_flags": "XR"}, "disabled", "X")
    assert not to_bool({}, "disabled", "X")
    assert to_optional_bool({}, "hw") is None
    assert to_optional_bool({"_flags": ""}, "hw-offload", "H") is False
    assert to_bytes("1.5GiB") == int(1.5 * 1024**3)
    assert to_bytes("512KiB") == 512 * 1024
    assert to_bytes("123") == 123
    assert to_bytes("n/a") == 0 and to_bytes(None) == 0


def test_dynamic_service_rows_are_not_services():
    """7.20.7 lists other features' listeners and every open connection in /ip service."""
    replies = rb4011_v7_api()
    replies["/ip/service/print"] = [
        *replies["/ip/service/print"],
        {"name": "resolver", "port": "53", "disabled": "no", "dynamic": "yes"},
        {"name": "api-ssl", "port": "8729", "disabled": "no", "dynamic": "yes",
         "connection": "yes"},
        {"_flags": "Dc", "name": "ssh", "port": "22"},
    ]
    inv = collect_inventory(FakeTransport(replies))
    assert [(s.name, s.certificate) for s in inv.services] == [("ssh", ""),
                                                               ("api-ssl", "api-cert")]


# ------------------------------------------------------------------ radios


def test_a_device_with_no_radio_still_counts_as_asked():
    """The distinction the update stands on: an empty snapshot that was *taken* says
    "no radio", and one that was never taken says nothing at all."""
    inv = collect_inventory(FakeTransport(rb4011_v7_api()))
    assert inv.radios.collected and not inv.radios.has_radios
    assert inv.radios.menus == []  # no wireless package on this board at all

    # ... and a menu that exists but holds nothing is a third, different answer.
    with_driver = dict(rb4011_v7_api())
    with_driver["/interface/wifi/print"] = []
    empty = collect_inventory(FakeTransport(with_driver))
    assert empty.radios.menus == [WirelessMenu.WIFI] and not empty.radios.has_radios


def test_a_legacy_antenna_is_read_off_interface_wireless():
    replies = dict(rb4011_v7_api())
    replies["/interface/wireless/print"] = [
        {"name": "wlan1", "mode": "station-bridge", "band": "5ghz-a/n/ac",
         "ssid": "ptp-link", "mac-address": "AA:BB:CC:00:00:01", "running": "yes",
         "disabled": "no"},
    ]
    inv = collect_inventory(FakeTransport(replies))
    radio = inv.radios.interfaces[0]
    assert (radio.name, radio.menu, radio.role) == ("wlan1", WirelessMenu.WIRELESS,
                                                    WirelessRole.STATION)
    assert radio.running and radio.band == "5ghz-a/n/ac" and radio.ssid == "ptp-link"
    assert inv.radios.has_radios and not inv.radios.serves_stations
    assert inv.wireless == WirelessStack.WIRELESS


def test_an_ap_is_recognised_and_its_stations_counted():
    replies = dict(rb4011_v7_api())
    replies["/interface/wireless/print"] = [
        {"name": "wlan1", "mode": "ap-bridge", "running": "yes"},
        {"name": "wlan2", "mode": "ap-bridge", "running": "yes"},
    ]
    replies["/interface/wireless/registration-table/print"] = [
        {"interface": "wlan1", "mac-address": "AA:BB:CC:00:00:01"},
        {"interface": "wlan1", "mac-address": "AA:BB:CC:00:00:02"},
        {"interface": "wlan2", "mac-address": "AA:BB:CC:00:00:03"},
    ]
    inv = collect_inventory(FakeTransport(replies))
    assert inv.radios.serves_stations
    assert inv.radios.registrations == {"wlan1": 2, "wlan2": 1}
    assert inv.radios.running_names == ["wlan1", "wlan2"]


def test_the_new_driver_reports_its_mode_in_the_configuration():
    """`/interface wifi` keeps the mode in the configuration attached to the interface, so
    it arrives dotted. Which spelling the API uses is NUTNO OVĚŘIT; every plausible one is
    read, and a mode that is not found leaves the role UNKNOWN rather than guessed."""
    replies = dict(rb4011_v7_api())
    replies["/interface/wifi/print"] = [
        {"name": "wifi1", "configuration.mode": "ap", "running": "yes"},
        {"name": "wifi2", "running": "no"},  # nothing says what this one does
    ]
    inv = collect_inventory(FakeTransport(replies))
    roles = {r.name: r.role for r in inv.radios.interfaces}
    assert roles == {"wifi1": WirelessRole.AP, "wifi2": WirelessRole.UNKNOWN}
    assert inv.wireless == WirelessStack.WIFI


def test_sixty_gigahertz_names_its_one_peer_on_the_interface():
    """A w60g link has no registration table — one peer, in `remote-address`."""
    replies = dict(rb4011_v7_api())
    replies["/interface/w60g/print"] = [
        {"name": "wlan60-1", "mode": "bridge", "remote-address": "AA:BB:CC:00:00:09",
         "running": "yes"},
        {"name": "wlan60-2", "mode": "bridge", "running": "no"},  # nothing on the far end
    ]
    inv = collect_inventory(FakeTransport(replies))
    assert inv.radios.menus == [WirelessMenu.W60G]
    assert inv.radios.registrations == {"wlan60-1": 1}
    assert inv.wireless == WirelessStack.WIRELESS  # the `wireless` package drives 60 GHz


def test_both_stacks_at_once_are_both_recorded():
    replies = dict(rb4011_v7_api())
    replies["/interface/wireless/print"] = [{"name": "wlan1", "mode": "ap-bridge"}]
    replies["/interface/wifi/print"] = [{"name": "wifi1", "configuration.mode": "ap"}]
    inv = collect_inventory(FakeTransport(replies))
    assert inv.radios.menus == [WirelessMenu.WIRELESS, WirelessMenu.WIFI]
    assert inv.radios.names == ["wifi1", "wlan1"]


def test_v6_is_never_asked_about_interface_wifi():
    """The menu does not exist on v6, and asking costs a round trip and a log line."""
    fake = FakeTransport(hap_v6_ssh(), kind=TransportKind.SSH)
    collect_inventory(fake)
    assert "/interface/wifi/print" not in [path for path, _ in fake.calls]
    assert "/interface/w60g/print" in [path for path, _ in fake.calls]


def test_a_registration_table_is_not_asked_for_without_an_interface():
    """Nothing is on the air, so there is nothing to ask about."""
    replies = dict(rb4011_v7_api())
    replies["/interface/wifi/print"] = []
    fake = FakeTransport(replies)
    collect_inventory(fake)
    assert "/interface/wifi/registration-table/print" not in [p for p, _ in fake.calls]


def test_the_mode_of_an_unknown_kind_is_other_not_a_station():
    assert role_of("alignment-only") == WirelessRole.OTHER
    assert role_of("nstreme-dual-slave") == WirelessRole.OTHER
    assert role_of("station-pseudobridge") == WirelessRole.STATION
    assert role_of("wds-slave") == WirelessRole.AP
    assert role_of("") == WirelessRole.UNKNOWN
