from ipaddress import IPv4Address, IPv4Interface

import pytest
from pydantic import SecretStr, ValidationError

from factories import make_inventory, router_profile
from mqs.issues import Issue, Severity, codes, has_errors
from mqs.messages_cs import format_message
from mqs.model.bridge import DEFAULT_LAN_ADDRESS, BridgeConfig, LanAddressing, LanConfig
from mqs.model.inventory import Ipv6PackageState, PackageInfo
from mqs.model.network import Ipv4Range, usable_host
from mqs.model.profile import DeviceProfile
from mqs.model.services import DEFAULT_HARDENING_V7, ServicesConfig
from mqs.model.wan import PppoeWan, WanConfig, WanType


def test_default_profile_json_roundtrip():
    profile = router_profile()
    restored = DeviceProfile.model_validate_json(profile.model_dump_json())
    assert restored == profile


def test_pppoe_password_never_serialised():
    wan = WanConfig(settings=PppoeWan(username="u", password=SecretStr("tajne")))
    profile = DeviceProfile(wan=wan)
    dumped = profile.model_dump_json()
    assert "tajne" not in dumped
    assert "password" not in dumped
    restored = DeviceProfile.model_validate_json(dumped)
    assert restored.wan.settings.password.get_secret_value() == ""


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        DeviceProfile.model_validate({"identity": "x", "unknown": 1})


def test_bridge_ranges():
    with pytest.raises(ValidationError):
        BridgeConfig(pvid=0)
    with pytest.raises(ValidationError):
        BridgeConfig(priority=70000)


def test_wan_with_type_defaults():
    wan = WanConfig()
    assert wan.type == WanType.DHCP
    pppoe = wan.with_type(WanType.PPPOE)
    assert pppoe.type == WanType.PPPOE
    assert pppoe.mss_clamping is True
    static = pppoe.with_type(WanType.STATIC)
    assert static.mss_clamping is False
    assert static.with_type(WanType.STATIC) is static
    assert static.with_type(WanType.DHCP).type == WanType.DHCP


def test_lan_with_addressing():
    empty = LanConfig(addressing=LanAddressing.DHCP_CLIENT, address=None)
    static = empty.with_addressing(LanAddressing.STATIC)
    assert static.address == DEFAULT_LAN_ADDRESS  # always something to edit
    assert static.with_addressing(LanAddressing.STATIC) is static

    configured = LanConfig(address=IPv4Interface("10.0.0.1/24"), gateway=IPv4Address("10.0.0.254"))
    assert configured.dhcp_server.enabled
    dropped = configured.with_addressing(LanAddressing.DHCP_CLIENT)
    assert dropped.gateway is None  # both need a static address of their own
    assert not dropped.dhcp_server.enabled
    assert dropped.address == IPv4Interface("10.0.0.1/24")  # kept for switching back
    assert dropped.with_addressing(LanAddressing.NONE).address == IPv4Interface("10.0.0.1/24")


def test_ipv4_range():
    r = Ipv4Range(start=IPv4Address("10.0.0.10"), end=IPv4Address("10.0.0.20"))
    assert IPv4Address("10.0.0.15") in r
    assert IPv4Address("10.0.0.21") not in r
    assert "10.0.0.15" not in r
    assert str(r) == "10.0.0.10-10.0.0.20"
    with pytest.raises(ValidationError):
        Ipv4Range(start=IPv4Address("10.0.0.20"), end=IPv4Address("10.0.0.10"))


@pytest.mark.parametrize(
    ("iface", "expected"),
    [
        ("192.168.1.1/24", True),
        ("192.168.1.0/24", False),
        ("192.168.1.255/24", False),
        ("10.0.0.0/31", True),
        ("10.0.0.5/32", True),
    ],
)
def test_usable_host(iface, expected):
    assert usable_host(IPv4Interface(iface)) is expected


def test_services_default_hardening():
    services = ServicesConfig()
    assert services.hardening_text == DEFAULT_HARDENING_V7
    assert services.hardening_is_default
    assert DEFAULT_HARDENING_V7.startswith("/snmp community\n")
    assert "add address=195.113.144.238" in DEFAULT_HARDENING_V7

    edited = ServicesConfig(hardening_text="/ip dns\r\nset servers=1.1.1.1\r\n")
    assert edited.hardening_text == "/ip dns\nset servers=1.1.1.1\n"
    assert not edited.hardening_is_default
    assert edited.reset_hardening().hardening_is_default


def test_inventory_ipv6_state():
    assert make_inventory("7.15").ipv6_package_state == Ipv6PackageState.BUILTIN
    assert make_inventory("6.49").ipv6_package_state == Ipv6PackageState.MISSING
    enabled = make_inventory("6.49", packages=[PackageInfo(name="ipv6")])
    assert enabled.ipv6_package_state == Ipv6PackageState.ENABLED
    disabled = make_inventory("6.49", packages=[PackageInfo(name="ipv6", disabled=True)])
    assert disabled.ipv6_package_state == Ipv6PackageState.DISABLED


def test_inventory_ports_and_switches():
    inv = make_inventory(
        ports=["ether1", "sfp1", "wlan1", "bridge", "vlan10"],
        port_types={"wlan1": "wlan", "bridge": "bridge", "vlan10": "vlan"},
        switches={"switch1": "RTL8367"},
        port_switch={"ether1": "switch1"},
    )
    assert inv.port_names == ["ether1", "sfp1", "wlan1"]
    assert inv.interface("ether1").switch == "switch1"
    assert inv.interface("nope") is None
    assert inv.switch_type("switch1") == "RTL8367"
    assert inv.switch_type("switch9") is None
    assert inv.switch_type(None) is None
    assert inv.version.major == 7


def test_issue_message_and_helpers():
    issue = Issue("wan.interface_in_bridge", Severity.ERROR, "wan.interface", {"interface": "e5"})
    assert "e5" in issue.message
    assert str(issue).startswith("error [wan.interface]: ")
    warn = Issue("bridge.no_ports", Severity.WARNING)
    assert not has_errors([warn])
    assert has_errors([warn, issue])
    assert codes([warn, issue]) == {"bridge.no_ports", "wan.interface_in_bridge"}


def test_format_message_fallbacks():
    assert format_message("no.such.code") == "no.such.code"
    # Missing params must not crash; template is returned unformatted.
    assert "{interface}" in format_message("wan.interface_in_bridge", {})
