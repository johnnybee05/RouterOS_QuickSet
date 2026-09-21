from factories import make_inventory
from fake_transport import FakeTransport
from mqs.apply.verify import compare_offload, read_bridge_ports, verify_configuration
from mqs.capabilities.matrix import Capabilities
from mqs.capabilities.offload import plan_offload
from mqs.generator.base import generate
from mqs.generator.context import TargetContext
from mqs.issues import codes
from mqs.model.bridge import BridgeConfig
from mqs.model.inventory import BridgePortState
from mqs.model.presets import apply_preset
from mqs.model.profile import DeviceProfile, Mode

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
    ports = [{"interface": f"ether{i}", "bridge": "bridge", "hw": "yes", "hw-offload": "yes"}
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
        "/interface/bridge/print": [{"name": "bridge", "protocol-mode": "rstp"}],
        "/ip/address/print": [{"address": "192.168.88.1/24", "interface": "bridge"}],
        "/interface/bridge/port/print": ports,
    }
    replies.update(overrides)
    return FakeTransport(replies)


def test_all_checks_ok():
    profile, result = setup()
    checks, issues = verify_configuration(device_after_apply(), profile, result)
    assert [c.code for c in checks] == ["verify.identity", "verify.services", "verify.bridge",
                                        "verify.lan_address", "verify.hw_offload"]
    assert all(c.ok for c in checks), [c.message for c in checks if not c.ok]
    assert issues == []


def test_failed_checks_are_reported():
    profile, result = setup()
    fake = device_after_apply(**{
        "/system/identity/print": [{"name": "MikroTik"}],
        "/ip/service/print": [{"name": "ssh", "port": "22", "disabled": "no"},
                              {"name": "gopher", "port": "70"}],
        "/interface/bridge/print": [{"name": "bridge", "protocol-mode": "none"}],
        "/ip/address/print": [{"address": "bogus", "interface": "bridge"}],
    })
    checks, _ = verify_configuration(fake, profile, result)
    failed = {c.code: c for c in checks if not c.ok}
    assert set(failed) == {"verify.identity", "verify.services", "verify.bridge",
                           "verify.lan_address"}
    assert "MikroTik" in failed["verify.identity"].message
    assert failed["verify.services"].params["services"] == "ssh"
    assert "none" in failed["verify.bridge"].message


def test_offload_silently_not_running_is_reported_with_reason():
    profile, result = setup()
    ports = [{"interface": f"ether{i}", "bridge": "bridge", "hw": "yes",
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
                                 "/interface/bridge/print": [{"name": "bridge",
                                                              "protocol-mode": "rstp"}]})
    checks, issues = verify_configuration(fake, profile, result)
    assert [c.code for c in checks] == ["verify.identity", "verify.services", "verify.bridge"]
    assert issues == []


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
