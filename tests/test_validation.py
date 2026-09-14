from ipaddress import IPv4Address, IPv4Interface

import pytest
from pydantic import SecretStr

from factories import make_inventory, router_profile
from mqs.issues import Severity, codes, has_errors
from mqs.model.bridge import LanAddressing
from mqs.model.network import Ipv4Range
from mqs.model.profile import Mode
from mqs.model.validation import validate_profile
from mqs.model.wan import PppoeWan, StaticWan


def _update(model, **changes):
    return model.model_copy(update=changes)


def _with_wan_settings(profile, settings):
    return _update(profile, wan=_update(profile.wan, settings=settings))


def _with_dhcp(profile, **changes):
    dhcp = _update(profile.lan.dhcp_server, **changes)
    return _update(profile, lan=_update(profile.lan, dhcp_server=dhcp))


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


def test_router_mode_without_wan_warns():
    profile = router_profile()
    profile = _update(profile, wan=_update(profile.wan, enabled=False), mode=Mode.ROUTER)
    assert "mode.router_without_wan" in codes(validate_profile(profile))
