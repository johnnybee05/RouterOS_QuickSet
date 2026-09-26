from ipaddress import IPv4Address, IPv4Interface

from factories import make_inventory
from fake_transport import FakeTransport
from mqs.apply.verify import (
    check_import_script,
    compare_offload,
    expected_dhcp_clients,
    expected_gateways,
    read_bridge_ports,
    verify_configuration,
)
from mqs.capabilities.matrix import Capabilities
from mqs.capabilities.offload import plan_offload
from mqs.generator.base import generate
from mqs.generator.context import TargetContext
from mqs.issues import codes
from mqs.model.bridge import BridgeConfig
from mqs.model.inventory import BridgePortState
from mqs.model.presets import apply_preset
from mqs.model.profile import DeviceProfile, Mode
from mqs.model.wan import StaticWan, WanType
from mqs.transport.cli_output import parse_log

PORTS = [f"ether{i}" for i in range(1, 11)]


def rb4011_inventory():
    port_switch = {f"ether{i}": "switch1" for i in range(1, 6)}
    port_switch |= {f"ether{i}": "switch2" for i in range(6, 11)}
    return make_inventory("7.15.3", PORTS, switches={"switch1": "RTL8367",
                                                     "switch2": "RTL8367"},
                          port_switch=port_switch)


def setup():
    profile = apply_preset(DeviceProfile(identity="PV-R1"), Mode.ROUTER, PORTS)
    inventory = rb4011_inventory()
    result = generate(profile, TargetContext.from_inventory(inventory))
    return profile, result


def device_after_apply(**overrides):
    ports = [{"interface": f"ether{i}", "bridge": "bridge1", "hw": "yes", "hw-offload": "yes"}
             for i in range(2, 11)]
    replies = {
        "/system/identity/print": [{"name": "PV-R1"}],
        "/ip/service/print": [
            {"name": "telnet", "port": "23", "disabled": "yes"},
            {"name": "ftp", "port": "21", "disabled": "yes"},
            {"name": "www", "port": "80", "disabled": "yes"},
            {"name": "ssh", "port": "122", "disabled": "no"},
            {"name": "www-ssl", "port": "443", "disabled": "yes"},
            {"name": "api", "port": "8728", "disabled": "yes"},
            {"name": "winbox", "port": "8291", "disabled": "no"},
            {"name": "api-ssl", "port": "8729", "disabled": "no"},
        ],
        "/interface/bridge/print": [{"name": "bridge1", "protocol-mode": "none"}],
        "/ip/address/print": [{"address": "192.168.88.1/24", "interface": "bridge1"}],
        "/interface/bridge/port/print": ports,
        # The router preset takes its WAN address by DHCP, with the route that comes with it.
        "/ip/dhcp-client/print": [{"interface": "ether1", "disabled": "no", "status": "bound"}],
        "/ip/route/print": [{"dst-address": "0.0.0.0/0", "gateway": "10.0.0.1",
                             "dynamic": "yes"}],
    }
    replies.update(overrides)
    return FakeTransport(replies)


def test_all_checks_ok():
    profile, result = setup()
    checks, issues = verify_configuration(device_after_apply(), profile, result)
    assert [c.code for c in checks] == ["verify.identity", "verify.services", "verify.bridge",
                                        "verify.lan_address", "verify.dhcp_client",
                                        "verify.hw_offload"]
    assert all(c.ok for c in checks), [c.message for c in checks if not c.ok]
    assert issues == []


def test_failed_checks_are_reported():
    profile, result = setup()
    fake = device_after_apply(**{
        "/system/identity/print": [{"name": "MikroTik"}],
        "/ip/service/print": [{"name": "ssh", "port": "22", "disabled": "no"},
                              {"name": "gopher", "port": "70"}],
        "/interface/bridge/print": [{"name": "bridge1", "protocol-mode": "rstp"}],
        "/ip/address/print": [{"address": "bogus", "interface": "bridge1"}],
    })
    checks, _ = verify_configuration(fake, profile, result)
    failed = {c.code: c for c in checks if not c.ok}
    assert set(failed) == {"verify.identity", "verify.services", "verify.bridge",
                           "verify.lan_address"}
    assert "MikroTik" in failed["verify.identity"].message
    assert failed["verify.services"].params["services"] == "ssh"
    assert "rstp" in failed["verify.bridge"].message


def test_offload_silently_not_running_is_reported_with_reason():
    profile, result = setup()
    ports = [{"interface": f"ether{i}", "bridge": "bridge1", "hw": "yes",
              "hw-offload": "no" if i == 7 else "yes"} for i in range(2, 10)]
    fake = device_after_apply(**{"/interface/bridge/port/print": ports})
    checks, issues = verify_configuration(fake, profile, result)
    assert not next(c for c in checks if c.code == "verify.hw_offload").ok
    assert {"verify.offload_not_active", "verify.offload_port_missing"} <= codes(issues)
    not_active = next(i for i in issues if i.code == "verify.offload_not_active")
    assert not_active.params["port"] == "ether7"
    assert "RTL8367" in not_active.message


def test_offload_unreadable_and_unexpected():
    profile, result = setup()
    replies = device_after_apply().replies
    del replies["/interface/bridge/port/print"]
    checks, issues = verify_configuration(FakeTransport(replies), profile, result)
    assert codes(issues) == {"verify.offload_unreadable"}
    assert not checks[-1].ok

    caps = Capabilities.from_inventory(make_inventory("7.15.3", ["ether1", "sfp1"],
                                                      switches={"switch1": "QCA8337"},
                                                      port_switch={"ether1": "switch1"}))
    plan = plan_offload(BridgeConfig(ports=["ether1", "sfp1"]), caps)
    actual = [BridgePortState(interface="ether1", bridge="b", hw_offload=True),
              BridgePortState(interface="sfp1", bridge="b", hw_offload=True)]
    assert codes(compare_offload(plan, actual)) == {"verify.offload_unexpected"}


def test_read_bridge_ports_from_ssh_flags():
    fake = FakeTransport({"/interface/bridge/port/print": [
        {"_flags": "H", "interface": "ether2", "bridge": "bridge"},
        {"_flags": "X", "interface": "ether3", "bridge": "bridge", "hw": "no"},
    ]})
    states = read_bridge_ports(fake)
    assert states[0].hw_offload is True and states[1].hw_offload is False
    assert states[1].disabled and states[1].hw is False
    assert fake.calls[0] == ("/interface/bridge/port/print", {"detail": True})


def test_bridge_ap_profile_skips_address_and_offload_offline():
    profile = apply_preset(DeviceProfile(identity="AP"), Mode.BRIDGE_AP, ["ether1", "ether2"])
    result = generate(profile, TargetContext.offline("7.15.3"))
    fake = device_after_apply(**{"/system/identity/print": [{"name": "AP"}],
                                 "/interface/bridge/print": [{"name": "bridge1",
                                                              "protocol-mode": "none"}],
                                 "/ip/dhcp-client/print": [{"interface": "bridge1",
                                                            "status": "searching..."}]})
    checks, issues = verify_configuration(fake, profile, result)
    assert [c.code for c in checks] == ["verify.identity", "verify.services", "verify.bridge",
                                        "verify.dhcp_client"]
    # Written and enabled is what the check judges; the lease may still be on its way.
    assert checks[-1].ok and "searching" in checks[-1].message
    assert issues == []


def static_wan_profile():
    profile, _ = setup()
    wan = profile.wan.with_type(WanType.STATIC)
    wan = wan.model_copy(update={"settings": StaticWan(address=IPv4Interface("192.0.2.2/24"),
                                                       gateway=IPv4Address("192.0.2.1"))})
    profile = profile.model_copy(update={"wan": wan})
    return profile, generate(profile, TargetContext.from_inventory(rb4011_inventory()))


def test_the_static_default_routes_are_exactly_the_profiles():
    profile, result = static_wan_profile()
    assert expected_gateways(profile) == ["192.0.2.1"] and expected_dhcp_clients(profile) == []

    def route_check(rows):
        checks, _ = verify_configuration(device_after_apply(**{"/ip/route/print": rows}),
                                         profile, result)
        return next(c for c in checks if c.code == "verify.default_route")

    ours = {"dst-address": "0.0.0.0/0", "gateway": "192.0.2.1", "dynamic": "no"}
    connected = {"dst-address": "192.0.2.0/24", "gateway": "ether1", "dynamic": "yes"}
    assert route_check([ours, connected]).ok
    # Over SSH the state comes as flags; a disabled leftover does not count.
    assert route_check([{"_flags": "As", "dst-address": "0.0.0.0/0", "gateway": "192.0.2.1"},
                        {"_flags": "X", "dst-address": "0.0.0.0/0", "gateway": "192.0.2.7"},
                        {"_flags": "DAd", "dst-address": "0.0.0.0/0", "gateway": "10.0.0.1"}]).ok
    # What the hEX S had after the import: the old route next to the new one.
    both = route_check([ours, {**ours, "gateway": "192.0.2.9"}])
    assert not both.ok and both.params == {"expected": "192.0.2.1",
                                           "actual": "192.0.2.1, 192.0.2.9"}
    assert not route_check([connected]).ok
    assert "{" not in both.message


def test_a_missing_or_disabled_dhcp_client_fails():
    profile, result = setup()
    for rows in ([], [{"interface": "ether1", "disabled": "yes"}]):
        checks, _ = verify_configuration(
            device_after_apply(**{"/ip/dhcp-client/print": rows}), profile, result)
        client = next(c for c in checks if c.code == "verify.dhcp_client")
        assert not client.ok and client.params["interface"] == "ether1"


def test_an_open_connection_does_not_stand_in_for_its_service():
    """7.20.7 lists every open connection as a dynamic /ip service row under the name of
    its service, always enabled. Read by name, the last row won: here the connection
    hid that api-ssl itself had been switched off."""
    profile, result = setup()
    rows = [dict(r) for r in device_after_apply().replies["/ip/service/print"]]
    rows[-1]["disabled"] = "yes"  # api-ssl
    rows.append({"name": "api-ssl", "port": "8729", "disabled": "no", "dynamic": "yes",
                 "connection": "yes", "local": "192.168.55.68",
                 "remote": "192.168.55.55:62681"})
    rows.append({"_flags": "Dc", "name": "ssh", "port": "22", "local": "192.168.55.68",
                 "remote": "192.168.55.55:61425"})  # the same row read over SSH
    checks, _ = verify_configuration(device_after_apply(**{"/ip/service/print": rows}),
                                     profile, result)
    services = next(c for c in checks if c.code == "verify.services")
    assert services.params["services"] == "api-ssl"


def test_the_stopped_import_is_quoted_from_a_7_23_7_log():
    """7.23.7 over SSH (hEX S, 2026-09-26) writes "Script Error" after the session, and
    the check quoted nothing ("chyba: ?")."""
    log = parse_log(
        " 2026-09-26 14:32:00 script,error executing script from sshd failed, please check"
        " it manually\r\n"
        " 2026-09-26 14:32:00 script,error,debug (ssh-cmd:bee+cet@192.168.55.55) Script"
        " Error: failure: mstp requires vlan-filtering (/interface/bridge/set *0 = *0)"
        " (/interface/bridge/set; line 18) (:import; line 1)\r\n")
    fake = FakeTransport({"/file/print": [{"name": "quickset-1.rsc"}], "/log/print": log})
    check = check_import_script(fake, "quickset-1.rsc")
    assert not check.ok
    assert check.params["error"] == (
        "Script Error: failure: mstp requires vlan-filtering (/interface/bridge/set *0 = *0)"
        " (/interface/bridge/set; line 18) (:import; line 1)")
