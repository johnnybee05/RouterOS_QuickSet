from ipaddress import IPv4Address, IPv4Interface

import pytest
from pydantic import SecretStr

from factories import FIVE_PORTS, make_inventory, router_profile
from mqs.issues import Severity, codes, has_errors
from mqs.model.bridge import LanAddressing
from mqs.model.network import Ipv4Range
from mqs.model.ports import rename_port
from mqs.model.presets import apply_preset
from mqs.model.profile import DeviceProfile, Mode
from mqs.model.validation import validate_profile
from mqs.model.wan import PppoeWan, StaticWan


def _update(model, **changes):
    return model.model_copy(update=changes)


def _with_wan_settings(profile, settings):
    return _update(profile, wan=_update(profile.wan, settings=settings))


def _with_dhcp(profile, **changes):
    dhcp = _update(profile.lan.dhcp_server, **changes)
    return _update(profile, lan=_update(profile.lan, dhcp_server=dhcp))


def _with_lan(profile, **changes):
    return _update(profile, lan=_update(profile.lan, **changes))


def _static(address, gateway):
    return StaticWan(address=IPv4Interface(address), gateway=IPv4Address(gateway))


def test_router_preset_is_valid():
    profile = router_profile()
    issues = validate_profile(profile, make_inventory())
    assert not has_errors(issues), [str(i) for i in issues]


@pytest.mark.parametrize(
    ("identity", "code"),
    [
        ("", "identity.empty"),
        ("x" * 65, "identity.too_long"),
        ("Router Praha", "identity.invalid_chars"),
        ("Směrovač", "identity.invalid_chars"),
    ],
)
def test_identity_negative(identity, code):
    assert code in codes(validate_profile(router_profile(identity=identity)))


def test_identity_accepts_allowed_chars():
    assert not codes(validate_profile(router_profile(identity="PV-rb4011_01.lab"))) & {
        "identity.invalid_chars",
        "identity.too_long",
    }


def test_wan_port_in_bridge():
    profile = router_profile()
    profile = _update(profile, bridge=_update(profile.bridge, ports=["ether1", "ether2"]))
    issues = validate_profile(profile)
    assert "wan.interface_in_bridge" in codes(issues)
    issue = next(i for i in issues if i.code == "wan.interface_in_bridge")
    assert issue.field == "wan.interface"
    assert "ether1" in issue.message


def test_wan_disabled_skips_wan_checks():
    profile = router_profile()
    profile = _update(
        profile,
        wan=_update(profile.wan, enabled=False),
        bridge=_update(profile.bridge, ports=["ether1", "ether2"]),
    )
    assert not {c for c in codes(validate_profile(profile)) if c.startswith("wan.")}


def test_wan_no_interface_and_unknown():
    profile = router_profile(ports=[])
    assert "wan.no_interface" in codes(validate_profile(profile))
    profile = router_profile()
    profile = _update(profile, wan=_update(profile.wan, interface="ether9"))
    assert "wan.interface_unknown" in codes(validate_profile(profile, make_inventory()))
    assert "wan.interface_unknown" not in codes(validate_profile(profile))


def test_static_gateway_outside_subnet():
    settings = _static("203.0.113.10/29", "203.0.113.30")
    issues = codes(validate_profile(_with_wan_settings(router_profile(), settings)))
    assert "wan.static.gateway_outside_subnet" in issues


def test_static_gateway_equals_address():
    settings = _static("203.0.113.10/29", "203.0.113.10")
    issues = codes(validate_profile(_with_wan_settings(router_profile(), settings)))
    assert "wan.static.gateway_is_address" in issues


def test_static_address_not_host():
    settings = _static("203.0.113.8/29", "203.0.113.9")
    issues = codes(validate_profile(_with_wan_settings(router_profile(), settings)))
    assert "wan.static.address_not_host" in issues


def test_static_valid():
    settings = _static("203.0.113.10/29", "203.0.113.9")
    assert not has_errors(validate_profile(_with_wan_settings(router_profile(), settings)))


def test_pppoe_negative_and_positive():
    bad = PppoeWan(interface_name="pppoe out")
    issues = codes(validate_profile(_with_wan_settings(router_profile(), bad)))
    assert {"wan.pppoe.username_empty", "wan.pppoe.password_empty",
            "wan.pppoe.interface_name_invalid"} <= issues

    good = PppoeWan(username="user@isp", password=SecretStr("secret"))
    assert not has_errors(validate_profile(_with_wan_settings(router_profile(), good)))


def test_bridge_duplicate_unknown_and_empty():
    profile = router_profile()
    profile = _update(profile, bridge=_update(profile.bridge, ports=["ether2", "ether2", "ether7"]))
    issues = codes(validate_profile(profile, make_inventory()))
    assert {"bridge.port_duplicate", "bridge.port_unknown"} <= issues

    profile = _update(profile, bridge=_update(profile.bridge, ports=[], name="my bridge"))
    issues = validate_profile(profile)
    assert "bridge.name_invalid" in codes(issues)
    no_ports = next(i for i in issues if i.code == "bridge.no_ports")
    assert no_ports.severity == Severity.WARNING


def test_lan_address_missing_and_not_host():
    profile = router_profile()
    missing = _update(profile, lan=_update(profile.lan, address=None))
    assert {"lan.address_missing", "dhcp.requires_static"} <= codes(validate_profile(missing))
    network = _update(profile, lan=_update(profile.lan, address=IPv4Interface("192.168.88.0/24")))
    assert "lan.address_not_host" in codes(validate_profile(network))


def test_lan_overlaps_static_wan():
    settings = _static("192.168.88.200/24", "192.168.88.254")
    profile = _with_wan_settings(router_profile(), settings)
    assert "lan.overlaps_wan" in codes(validate_profile(profile))


def _pool(start, end):
    return Ipv4Range(start=IPv4Address(start), end=IPv4Address(end))


def test_pool_outside_subnet():
    profile = _with_dhcp(router_profile(), pool=_pool("192.168.88.100", "192.168.89.10"))
    assert "dhcp.pool_outside_subnet" in codes(validate_profile(profile))


def test_pool_contains_bridge_address():
    profile = _with_dhcp(router_profile(), pool=_pool("192.168.88.1", "192.168.88.50"))
    assert "dhcp.pool_contains_address" in codes(validate_profile(profile))


def test_pool_contains_reserved():
    profile = _with_dhcp(router_profile(), pool=_pool("192.168.88.100", "192.168.88.255"))
    assert "dhcp.pool_contains_reserved" in codes(validate_profile(profile))


def test_dhcp_gateway_outside_subnet():
    profile = _with_dhcp(router_profile(), gateway=IPv4Address("10.0.0.1"))
    assert "dhcp.gateway_outside_subnet" in codes(validate_profile(profile))


def test_dhcp_requires_static_addressing():
    profile = router_profile()
    profile = _update(profile, lan=_update(profile.lan, addressing=LanAddressing.DHCP_CLIENT))
    assert "dhcp.requires_static" in codes(validate_profile(profile))


def test_lan_gateway_must_be_reachable():
    profile = router_profile()  # LAN 192.168.88.1/24
    same = _with_lan(profile, gateway=IPv4Address("192.168.88.1"))
    assert "lan.gateway_is_address" in codes(validate_profile(same))
    outside = _with_lan(profile, gateway=IPv4Address("10.9.9.1"))
    assert "lan.gateway_outside_subnet" in codes(validate_profile(outside))


def test_lan_gateway_warns_when_wan_already_routes():
    profile = _with_lan(router_profile(), gateway=IPv4Address("192.168.88.254"))
    issues = validate_profile(profile)
    assert "lan.gateway_conflicts_wan" in codes(issues)
    assert not has_errors(issues)

    no_route = _update(profile.wan, settings=_update(profile.wan.settings,
                                                     add_default_route=False))
    assert "lan.gateway_conflicts_wan" not in codes(
        validate_profile(_update(profile, wan=no_route)))


def test_bridge_ap_with_static_address_and_gateway_is_valid():
    """The Bridge / AP scenario the GUI produces: fixed management IP plus a default route."""
    profile = apply_preset(DeviceProfile(identity="AP1"), Mode.BRIDGE_AP, FIVE_PORTS)
    lan = _update(profile.lan.with_addressing(LanAddressing.STATIC),
                  address=IPv4Interface("192.168.88.10/24"),
                  gateway=IPv4Address("192.168.88.1"))
    issues = validate_profile(_update(profile, lan=lan), make_inventory())
    assert not has_errors(issues), [str(i) for i in issues]
    assert "lan.gateway_conflicts_wan" not in codes(issues)  # no WAN of its own


def test_lan_gateway_without_static_addressing_warns():
    profile = _with_lan(router_profile(), addressing=LanAddressing.DHCP_CLIENT,
                        gateway=IPv4Address("192.168.88.1"))
    issues = validate_profile(profile)
    assert "lan.gateway_requires_static" in codes(issues)
    gateway_issue = next(i for i in issues if i.code == "lan.gateway_requires_static")
    assert gateway_issue.severity == Severity.WARNING


def test_fasttrack_warnings():
    profile = router_profile()
    fasttrack = _update(profile.firewall.fasttrack, enabled=True)
    profile = _update(profile, firewall=_update(profile.firewall, fasttrack=fasttrack))
    issues = validate_profile(profile)
    assert "firewall.fasttrack_shaping_unconfirmed" in codes(issues)
    assert not has_errors(issues)

    confirmed = _update(fasttrack, no_shaping_confirmed=True)
    profile = _update(profile, firewall=_update(profile.firewall, fasttrack=confirmed))
    profile = _with_wan_settings(profile, PppoeWan(username="u", password=SecretStr("p")))
    issues = codes(validate_profile(profile))
    assert "firewall.fasttrack_shaping_unconfirmed" not in issues
    assert "firewall.fasttrack_pppoe_no_hw" in issues


def _remote_dns(profile, on=True):
    return _update(profile, services=_update(profile.services, dns_remote_requests=on))


def test_remote_dns_without_basic_filter_warns():
    profile = _remote_dns(router_profile())
    assert "dns.remote_requests_need_filter" not in codes(validate_profile(profile))
    profile = _update(profile, firewall=_update(profile.firewall, basic_filter=False))
    issues = validate_profile(profile)
    assert "dns.remote_requests_need_filter" in codes(issues)
    assert not has_errors(issues)


def test_dhcp_hands_out_router_that_does_not_answer():
    profile = _with_dhcp(router_profile(), dns_servers=[IPv4Address("192.168.88.1")])
    issue = next(i for i in validate_profile(profile)
                 if i.code == "dhcp.dns_router_not_answering")
    assert issue.field == "lan.dhcp_server.dns_servers"
    assert issue.params == {"address": "192.168.88.1"}
    assert "dhcp.dns_router_not_answering" not in codes(validate_profile(_remote_dns(profile)))


def test_router_mode_without_wan_warns():
    profile = router_profile()
    profile = _update(profile, wan=_update(profile.wan, enabled=False), mode=Mode.ROUTER)
    assert "mode.router_without_wan" in codes(validate_profile(profile))


def _named(profile, **names):
    return _update(profile, interface_names=names)


def test_ports_are_known_by_the_names_the_profile_gives_them():
    profile = rename_port(router_profile(), "ether2", "lan2")
    issues = validate_profile(profile, make_inventory())
    assert not has_errors(issues), [str(i) for i in issues]
    # Without the rename, the device has no port called lan2.
    assert "bridge.port_unknown" in codes(validate_profile(_named(profile), make_inventory()))


def test_port_name_collisions():
    profile = router_profile()
    duplicate = validate_profile(_named(profile, ether2="lan", ether3="lan"))
    assert "ports.name_duplicate" in codes(duplicate)
    # A swap: when ether1 is renamed, ether2 still has that name (and gets it after a reset).
    swap = [i for i in validate_profile(_named(profile, ether1="ether2", ether2="ether1"))
            if i.code == "ports.name_in_use"]
    assert [i.params for i in swap] == [{"default": "ether1", "name": "ether2",
                                         "other": "ether2"}]
    assert "ports.name_taken" in codes(validate_profile(_named(profile, ether2="bridge1")))
    assert "ports.name_empty" in codes(validate_profile(_named(profile, ether2="")))


def test_port_name_taken_on_the_device():
    profile = router_profile(["ether1", "ether2"])
    inventory = make_inventory("7.20.7", ["uplink", "ether2"], default_names={"uplink": "ether1"})
    # ether1 keeps "uplink", so ether2 cannot have it too.
    assert "ports.name_duplicate" in codes(validate_profile(_named(profile, ether2="uplink"),
                                                            inventory))
    # ether1 gives it up first (renames run in port order), so ether2 can have it.
    issues = validate_profile(_named(profile, ether1="wan", ether2="uplink"), inventory)
    assert not {"ports.name_in_use", "ports.name_duplicate"} & codes(issues)
    # The other way round, ether1's rename runs while ether2 still has the name.
    inventory = make_inventory("7.20.7", ["ether1", "uplink"], default_names={"uplink": "ether2"})
    issues = validate_profile(_named(profile, ether1="uplink", ether2="lan"), inventory)
    assert next(i for i in issues if i.code == "ports.name_in_use").params["other"] == "ether2"


def test_port_names_against_the_device():
    inventory = make_inventory("7.20.7", ["ether1", "ether2", "port 3"],
                               default_names={"port 3": "ether3"})
    profile = _named(router_profile(["ether1", "ether2", "port 3"]), ether3="port 3",
                     ether9="spare")
    issues = validate_profile(profile, inventory)
    # A name the device already uses is fine whatever its characters.
    assert "ports.name_unusual" not in codes(issues)
    unknown = next(i for i in issues if i.code == "ports.unknown")
    assert unknown.severity == Severity.WARNING and unknown.params["default"] == "ether9"
    unusual = validate_profile(_named(router_profile(), ether2="klient 2"))
    assert next(i for i in unusual if i.code == "ports.name_unusual").severity == Severity.WARNING
