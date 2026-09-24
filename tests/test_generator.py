from ipaddress import IPv4Address, IPv4Interface, IPv4Network

import pytest
from pydantic import SecretStr

from factories import FIVE_PORTS, make_inventory, router_profile
from mqs.capabilities.matrix import Capabilities
from mqs.generator import features
from mqs.generator.base import generate, router_is_dns
from mqs.generator.context import TargetContext
from mqs.generator.hardening import binding
from mqs.generator.ir import Command, SectionKey
from mqs.issues import codes
from mqs.model.bridge import HwOffloadMode, LanAddressing, ProtocolMode
from mqs.model.inventory import Ipv6PackageState, PackageInfo, ServiceInfo
from mqs.model.ports import adopt_device_names, rename_port
from mqs.model.presets import apply_preset
from mqs.model.profile import DeviceProfile, Mode
from mqs.model.wan import PppoeWan, StaticWan

# ---------------------------------------------------------------- scenarios


def _u(model, **changes):
    return model.model_copy(update=changes)


def router_dhcp() -> DeviceProfile:
    """Acceptance scenario: ether5 as WAN, the rest in an rstp bridge."""
    profile = router_profile(FIVE_PORTS, identity="PV-R1")
    return _u(profile,
              wan=_u(profile.wan, interface="ether5"),
              bridge=_u(profile.bridge, ports=["ether1", "ether2", "ether3", "ether4"]))


def router_static() -> DeviceProfile:
    profile = router_dhcp()
    settings = StaticWan(address=IPv4Interface("203.0.113.10/29"),
                         gateway=IPv4Address("203.0.113.9"),
                         dns_servers=[IPv4Address("1.1.1.1")])
    return _u(profile, wan=_u(profile.wan, settings=settings))


def router_pppoe_fasttrack() -> DeviceProfile:
    profile = router_dhcp()
    wan = profile.wan.with_type(PppoeWan(username="user@isp").type)
    wan = _u(wan, settings=PppoeWan(username="user@isp", password=SecretStr("s3cr3t")))
    fasttrack = _u(profile.firewall.fasttrack, enabled=True, hw_offload=True,
                   no_shaping_confirmed=True)
    return _u(profile, wan=wan, firewall=_u(profile.firewall, fasttrack=fasttrack))


def bridge_ap() -> DeviceProfile:
    profile = apply_preset(DeviceProfile(identity="PV-AP1"), Mode.BRIDGE_AP, FIVE_PORTS)
    return _u(profile, bridge=_u(profile.bridge, protocol_mode=ProtocolMode.NONE))


def bridge_ap_static() -> DeviceProfile:
    """AP with a fixed management address and a default route; it has no WAN of its own."""
    profile = bridge_ap()
    lan = _u(profile.lan.with_addressing(LanAddressing.STATIC),
             address=IPv4Interface("192.168.88.10/24"), gateway=IPv4Address("192.168.88.1"))
    return _u(profile, lan=lan)


def custom_hardening() -> DeviceProfile:
    profile = router_dhcp()
    text = profile.services.hardening_text
    text = binding.set_ssh_port(text, 2222)
    text = binding.set_dns_servers(text, ["1.1.1.1", "9.9.9.9"])
    text = binding.set_ntp_servers(text, ["195.113.144.201", "tik.cesnet.cz", "195.113.144.238"])
    text += "/ip proxy\nset enabled=no\n"
    services = _u(profile.services, hardening_text=text, robust_logging=True,
                  management_subnet=IPv4Network("10.10.0.0/24"))
    return _u(profile, services=services)


SCENARIOS = {
    "router_dhcp": router_dhcp,
    "router_static": router_static,
    "router_pppoe_fasttrack": router_pppoe_fasttrack,
    "bridge_ap": bridge_ap,
    "bridge_ap_static": bridge_ap_static,
    "custom_hardening": custom_hardening,
}


def target(version: str) -> TargetContext:
    if version.startswith("7"):
        return TargetContext.offline(version, probes={"fasttrack_hw_offload": True})
    ipv6 = Ipv6PackageState.ENABLED if version == "6.49.10" else Ipv6PackageState.MISSING
    return TargetContext.offline(version, ipv6_package=ipv6)


TARGETS = ["6.40.9", "6.49.10", "7.15.3"]


def _rb4011_inventory(version: str):
    port_switch = {f"ether{i}": "switch1" for i in range(1, 6)}
    port_switch |= {f"ether{i}": "switch2" for i in range(6, 11)}
    return make_inventory(
        version, [f"ether{i}" for i in range(1, 11)] + ["sfp-sfpplus1"],
        switches={"switch1": "RTL8367", "switch2": "RTL8367"}, port_switch=port_switch,
        board_name="RB4011iGS+",
        packages=[PackageInfo(name="ipv6")] if version.startswith("6") else [],
    )


def rb4011_profile() -> DeviceProfile:
    ports = [f"ether{i}" for i in range(1, 11)] + ["sfp-sfpplus1"]
    return router_profile(ports, identity="PV-RB4011")


def all_outputs():
    for name, build in SCENARIOS.items():
        for version in TARGETS:
            yield name, version, generate(build(), target(version))
    for version in ("6.40.9", "6.49.10", "7.15.3"):
        inv = _rb4011_inventory(version)
        yield "rb4011", version, generate(rb4011_profile(), TargetContext.from_inventory(inv))


OUTPUTS = list(all_outputs())
IDS = [f"{name}-{version}" for name, version, _ in OUTPUTS]

# ---------------------------------------------------------------- golden files


@pytest.mark.parametrize(("name", "version", "result"), OUTPUTS, ids=IDS)
def test_golden(golden, name, version, result):
    golden(f"{name}__{version}.rsc", result.text)


# ---------------------------------------------------------------- mandatory properties


@pytest.mark.parametrize(("name", "version", "result"), OUTPUTS, ids=IDS)
def test_ip_service_block_is_always_last(name, version, result):
    non_empty = [s.key for s in result.script.sections if s.items]
    assert non_empty == sorted(non_empty)
    assert non_empty[-1] == SectionKey.SERVICES
    lines = result.text.splitlines()
    service_header = lines.index("# ---- IP SERVICES ----")
    assert all(not line.startswith("# ---- ") for line in lines[service_header + 1:])
    menus_after = {line for line in lines[service_header + 1:] if line.startswith("/")}
    assert menus_after == {"/ip service"}
    assert lines[1:].index("# ---- IDENTITY ----") < 4  # identity comes right after the header


@pytest.mark.parametrize(("name", "version", "result"), OUTPUTS, ids=IDS)
def test_v6_output_rules(name, version, result):
    if not version.startswith("6"):
        return
    assert "hw-offload" not in result.text
    assert "::" not in result.text
    assert "/system ntp client servers" not in result.text
    assert "/ipv6 settings" not in result.text
    if version == "6.40.9":
        assert "hw=" not in result.text
        assert "interface-list" not in result.text
        assert "/interface list" not in result.text


@pytest.mark.parametrize(("name", "version", "result"), OUTPUTS, ids=IDS)
def test_fasttrack_is_followed_by_accept(name, version, result):
    commands = list(result.script.section(SectionKey.FIREWALL).commands())
    for i, command in enumerate(commands):
        if command.arg("action") == "fasttrack-connection":
            follower = commands[i + 1]
            assert follower.arg("chain") == "forward"
            assert follower.arg("action") == "accept"
            assert follower.arg("connection-state") == "established,related,untracked"


def test_deterministic():
    assert generate(custom_hardening(), target("7.15.3")).text == \
        generate(custom_hardening(), target("7.15.3")).text


# ---------------------------------------------------------------- translation details


def test_v6_ntp_translation():
    text = generate(router_dhcp(), target("6.49.10")).text
    assert "/system ntp client\nset enabled=yes primary-ntp=195.113.144.201 " \
           "secondary-ntp=195.113.144.238\n" in text


def test_v7_keeps_block_semantics():
    text = generate(router_dhcp(), target("7.15.3")).text
    assert "/system ntp client servers\nadd address=195.113.144.201\n" in text
    assert "/ipv6 settings\nset disable-ipv6=yes\n" in text
    assert "add addresses=::/0 name=PVIP" in text


def test_v6_ipv6_package_states():
    enabled = generate(router_dhcp(), target("6.49.10"))
    assert "/system package\ndisable ipv6\n" in enabled.text
    assert "v6.ipv6_package_disable" in codes(enabled.issues)

    missing = generate(router_dhcp(), target("6.40.9"))
    assert "ipv6" not in missing.text
    assert "v6.ipv6_omitted_missing" in codes(missing.issues)

    unknown = generate(router_dhcp(), TargetContext.offline("6.49"))
    assert "v6.ipv6_unknown_package" in codes(unknown.issues)


def test_v6_snmp_addresses():
    text = generate(router_dhcp(), target("6.49.10")).text
    assert "add addresses=0.0.0.0/0 name=PVIP" in text
    profile = router_dhcp()
    services = _u(profile.services, hardening_text=(
        "/snmp community\nadd addresses=::/0,10.0.0.0/8,2001:db8::/32 name=X\n"))
    result = generate(_u(profile, services=services), target("6.49.10"))
    assert "add addresses=0.0.0.0/0,10.0.0.0/8 name=X" in result.text
    assert "v6.snmp_ipv6_address" in codes(result.issues)
    services = _u(profile.services, hardening_text="/snmp community\nadd addresses=fe80::/64\n")
    result = generate(_u(profile, services=services), target("6.49.10"))
    assert "add addresses=0.0.0.0/0" in result.text


def test_v6_ntp_hostnames_and_limits():
    old = generate(custom_hardening(), target("6.40.9"))
    assert "v6.ntp_hostname_unsupported" in codes(old.issues)
    assert "server-dns-names" not in old.text
    new = generate(custom_hardening(), target("6.49.10"))
    assert "server-dns-names=tik.cesnet.cz" in new.text
    assert "v6.ntp_too_many" not in codes(new.issues)

    profile = router_dhcp()
    servers = ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
    text = binding.set_ntp_servers(profile.services.hardening_text, servers)
    many = generate(_u(profile, services=_u(profile.services, hardening_text=text)),
                    target("6.49.10"))
    assert "v6.ntp_too_many" in codes(many.issues)
    assert "primary-ntp=10.0.0.1 secondary-ntp=10.0.0.2" in many.text
    assert "10.0.0.3" not in many.text


def test_v6_ntp_odd_lines():
    profile = router_dhcp()
    text = "/system ntp client servers\nremove [ find ]\nadd address=10.0.0.1\n"
    result = generate(_u(profile, services=_u(profile.services, hardening_text=text)),
                      target("6.49.10"))
    assert "v6.ntp_dropped_line" in codes(result.issues)
    assert "set enabled=yes primary-ntp=10.0.0.1" in result.text


def test_v6_neighbor_discovery_and_raw_lines():
    old = generate(router_dhcp(), target("6.40.9"))
    assert "discover-interface-list" not in old.text
    assert "v6.feature_missing" in codes(old.issues)

    profile = router_dhcp()
    text = "/ipv6 address\nadd address=2001:db8::1/64 interface=bridge\n:delay 1s\n"
    result = generate(_u(profile, services=_u(profile.services, hardening_text=text)),
                      target("6.40.9"))
    assert {"v6.ipv6_dropped", "v6.raw_line_untranslated"} <= codes(result.issues)
    enabled = generate(_u(profile, services=_u(profile.services, hardening_text=text)),
                       target("6.49.10"))
    assert "add address=2001:db8::1/64 interface=bridge" in enabled.text


def test_v7_missing_feature_warning(monkeypatch):
    patched = features.Feature("ipv6_disable_setting", "test", min_v6=None, min_v7=(7, 99))
    monkeypatch.setitem(features.FEATURES, "ipv6_disable_setting", patched)
    result = generate(router_dhcp(), target("7.15.3"))
    assert "v7.feature_missing" in codes(result.issues)


def test_hardening_edit_propagates_to_both_dialects():
    profile = router_dhcp()
    text = binding.set_ssh_port(profile.services.hardening_text, 2200)
    profile = _u(profile, services=_u(profile.services, hardening_text=text))
    for version in ("6.49.10", "7.15.3"):
        result = generate(profile, target(version))
        assert "set ssh port=2200" in result.text
        assert result.access.ssh_port == 2200


# ---------------------------------------------------------------- blocks


def test_acceptance_router_v7_contents():
    result = generate(router_dhcp(), target("7.15.3"))
    text = result.text
    assert "/system identity\nset name=PV-R1\n" in text
    assert "add name=bridge protocol-mode=rstp" in text
    assert "add interface=ether5 list=WAN" in text
    assert "add interface=ether5 use-peer-dns=yes add-default-route=yes disabled=no" in text
    assert ('add chain=srcnat action=masquerade comment="quickset: masquerade to WAN" '
            "out-interface-list=WAN") in text
    assert "add name=dhcp1 interface=bridge address-pool=dhcp_pool lease-time=30m disabled=no" \
        in text
    assert "allow-remote-requests=yes" in text
    assert not result.has_errors, [str(i) for i in result.issues]


def test_firewall_order_and_wan_management():
    result = generate(router_dhcp(), target("7.15.3"))
    rules = result.script.find("/ip firewall filter", "add", chain="input")
    comments = [r.arg("comment") for r in rules]
    assert comments == [
        "quickset: accept established,related,untracked",
        "quickset: drop invalid",
        "quickset: accept ICMP",
        "quickset: accept to local loopback",
        "quickset: accept from LAN",
        "quickset: accept api-ssl from WAN",
        "quickset: accept SSH from WAN",
        "quickset: accept Winbox from WAN",
        "quickset: accept SNMP from WAN",
        "quickset: accept discovery from WAN",
        "quickset: drop all not coming from LAN",
    ]
    ports = {r.arg("comment"): (r.arg("protocol"), r.arg("dst-port"), r.arg("in-interface-list"))
             for r in rules if r.has_arg("dst-port")}
    assert ports["quickset: accept SSH from WAN"] == ("tcp", "122", "WAN")  # hardening port
    assert ports["quickset: accept api-ssl from WAN"] == ("tcp", "8729", "WAN")
    assert ports["quickset: accept Winbox from WAN"] == ("tcp", "8291", "WAN")
    assert ports["quickset: accept SNMP from WAN"] == ("udp", "161", "WAN")
    assert ports["quickset: accept discovery from WAN"] == ("udp", "5678", "WAN")
    # Earlier rules are replaced, and the device's own rules are moved below ours.
    text = result.text
    assert text.index('remove [ find where comment~"^quickset: " ]') < text.index("add chain=input")
    assert (":foreach i in=[ /ip firewall filter find where dynamic=no and "
            '!(comment~"^quickset: ") ] '
            "do={ /ip firewall filter move $i }") in text

    profile = router_dhcp()
    services = _u(profile.services, management_subnet=IPv4Network("10.0.0.0/24"))
    restricted = generate(_u(profile, services=services), target("7.15.3"))
    assert all(r.arg("src-address") == "10.0.0.0/24"
               for r in restricted.script.find("/ip firewall filter", "add", chain="input")
               if r.has_arg("dst-port"))

    closed = _u(profile, firewall=_u(profile.firewall, wan_management=False))
    closed_rules = generate(closed, target("7.15.3")).script.find("/ip firewall filter", "add")
    assert not any(r.has_arg("dst-port") for r in closed_rules)


def test_pppoe_block():
    result = generate(router_pppoe_fasttrack(), target("7.15.3"))
    assert "password=s3cr3t" in result.text
    assert "add interface=pppoe-out1 list=WAN" in result.text
    assert "action=change-mss" in result.text
    assert "hw-offload=yes" in result.text
    masked = generate(router_pppoe_fasttrack(), target("7.15.3"), mask_secrets=True)
    assert "s3cr3t" not in masked.text and 'password="********"' in masked.text

    no_probe = generate(router_pppoe_fasttrack(), TargetContext.offline("7.15.3"))
    assert "hw-offload" not in no_probe.text
    assert "fasttrack.hw_offload_skipped" in codes(no_probe.issues)

    old = generate(router_pppoe_fasttrack(), target("6.40.9"))
    assert "out-interface=pppoe-out1" in old.text


def test_static_wan_block():
    result = generate(router_static(), target("7.15.3"))
    assert "add dst-address=0.0.0.0/0 gateway=203.0.113.9" in result.text
    assert "wan.static.dns_overridden" in codes(result.issues)


def test_bridge_options_and_offload_offline():
    profile = router_dhcp()
    options = _u(profile.bridge, priority=0x1000, ageing_time_s=300, vlan_filtering=True,
                 pvid=10, use_ip_firewall=True, hw_offload=HwOffloadMode.OFF)
    result = generate(_u(profile, bridge=options), target("7.15.3"))
    assert ("add name=bridge protocol-mode=rstp priority=0x1000 ageing-time=5m "
            "vlan-filtering=yes pvid=10 frame-types=admit-all") in result.text
    assert "add bridge=bridge interface=ether1 pvid=10" in result.text
    assert "set [ find interface=ether1 ] hw=no" in result.text
    assert "/interface bridge settings\nset use-ip-firewall=yes" in result.text

    auto = generate(router_dhcp(), target("7.15.3"))
    assert "hw=" not in auto.text
    assert "offload.no_device_data" in codes(auto.issues)

    forced = _u(profile.bridge, hw_offload=HwOffloadMode.FORCE_ON)
    result = generate(_u(profile, bridge=forced), target("7.15.3"))
    assert "set [ find interface=ether4 ] hw=yes" in result.text


def test_rb4011_offload_lines():
    inv = _rb4011_inventory("7.15.3")
    result = generate(rb4011_profile(), TargetContext.from_inventory(inv))
    assert "set [ find interface=ether2 ] hw=yes" in result.text
    assert "set [ find interface=sfp-sfpplus1 ] hw=no" in result.text
    assert len(result.offload.groups) == 2
    assert "offload.software_bridge" in codes(result.issues)
    explicit = generate(rb4011_profile(), TargetContext.offline("7.15.3"),
                        capabilities=Capabilities.from_inventory(inv))
    assert explicit.text == result.text


def test_mstp_unsupported_version_and_conflict():
    profile = router_dhcp()
    profile = _u(profile, bridge=_u(profile.bridge, protocol_mode=ProtocolMode.MSTP))
    assert "bridge.mstp_unsupported_version" in codes(generate(profile, target("6.40.9")).issues)
    # QCA8337 does not offload MSTP (Bridge Hardware Offloading table).
    ports = [f"ether{i}" for i in range(1, 6)]
    inv = make_inventory("7.15.3", ports, switches={"switch1": "QCA8337"},
                         port_switch=dict.fromkeys(ports, "switch1"), board_name="RB3011UiAS")
    qca = apply_preset(DeviceProfile(identity="PV-R3"), Mode.ROUTER, ports)
    qca = _u(qca, bridge=_u(qca.bridge, protocol_mode=ProtocolMode.MSTP))
    result = generate(qca, TargetContext.from_inventory(inv))
    assert result.offload.has_conflict and result.has_errors
    assert "hw=yes" not in result.text

    # RTL8367 does offload MSTP, so the same setting is fine on an RB4011.
    rb = rb4011_profile()
    rb = _u(rb, bridge=_u(rb.bridge, protocol_mode=ProtocolMode.MSTP))
    ok = generate(rb, TargetContext.from_inventory(_rb4011_inventory("7.15.3")))
    assert not ok.offload.has_conflict and not ok.has_errors


def test_unsupported_version():
    result = generate(router_dhcp(), TargetContext.offline("6.39.3"))
    assert "target.unsupported_version" in codes(result.issues)


def test_bridge_ap_and_open_resolver():
    result = generate(bridge_ap(), target("7.15.3"))
    assert "/ip dhcp-client\nadd interface=bridge disabled=no" in result.text
    assert "/ip firewall" not in result.text
    assert not router_is_dns(bridge_ap())

    profile = router_dhcp()
    profile = _u(profile, firewall=_u(profile.firewall, basic_filter=False))
    assert "dns.open_resolver" in codes(generate(profile, target("7.15.3")).issues)


def test_bridge_ap_static_address_and_default_route():
    for version in TARGETS:
        result = generate(bridge_ap_static(), target(version))
        assert "/ip address\nadd address=192.168.88.10/24 interface=bridge\n" in result.text
        assert "/ip route\nadd dst-address=0.0.0.0/0 gateway=192.168.88.1\n" in result.text
        assert "/ip dhcp-client" not in result.text
        assert "/ip dhcp-server" not in result.text
        assert not result.has_errors, [str(i) for i in result.issues]


def test_lan_gateway_is_omitted_without_a_static_address():
    profile = bridge_ap_static()
    dhcp = _u(profile, lan=profile.lan.with_addressing(LanAddressing.DHCP_CLIENT))
    assert "/ip route" not in generate(dhcp, target("7.15.3")).text


def test_services_extensions_and_robust_logging():
    result = generate(custom_hardening(), target("7.15.3"))
    # A bare `find topics=info` matches nothing on 7.20.7: topics is an array.
    assert 'set [ find where topics~"^info\\$" ] action=disk' in result.text
    assert "set ssh address=10.10.0.0/24" in result.text
    assert "access.address_restricted" in codes(result.issues)

    profile = router_dhcp()
    services = _u(profile.services, api_ssl_certificate="api-cert", robust_logging=True,
                  hardening_text="/system logging\nset 7 action=disk\nadd topics=x\n")
    result = generate(_u(profile, services=services), target("7.15.3"))
    assert "set api-ssl certificate=api-cert" in result.text
    assert "logging.robust_unknown_index" in codes(result.issues)


def test_parse_problems_and_access_from_device():
    profile = router_dhcp()
    services = _u(profile.services, hardening_text=profile.services.hardening_text + "print\n")
    inv = make_inventory("7.15.3", FIVE_PORTS)
    inv = _u(inv, services=[
        ServiceInfo(name="api-ssl", port=8729, disabled=True),
        ServiceInfo(name="ssh", port=22),
        ServiceInfo(name="winbox", port=8291),
    ])
    result = generate(_u(profile, services=services), TargetContext.from_inventory(inv))
    assert "hardening.parse.unknown_verb" in codes(result.issues)
    assert "access.api_ssl_disabled" in codes(result.issues)


def test_no_header_and_timestamp():
    from datetime import datetime
    plain = generate(router_dhcp(), target("7.15.3"), header=False)
    assert plain.text.startswith("# ---- IDENTITY ----")
    stamped = generate(router_dhcp(), target("7.15.3"), now=datetime(2026, 9, 14, 12, 30))
    assert "# Generated: 2026-09-14 12:30" in stamped.text
    assert stamped.dialect == 7


def test_unverified_features_reported():
    result = generate(router_dhcp(), target("7.15.3"))
    issue = next(i for i in result.issues if i.code == "feature.unverified")
    assert "interface_list" in issue.params["features"]


def test_all_messages_have_templates():
    from mqs.messages_cs import MESSAGES
    for _, _, result in OUTPUTS:
        for issue in result.issues:
            assert issue.code in MESSAGES, issue.code
            assert "{" not in issue.message, issue.code


def test_commands_have_no_empty_values():
    for _, _, result in OUTPUTS:
        for command in result.script.commands():
            assert isinstance(command, Command)
            for key, value in command.args:
                assert key and value != "", (command, key)


def test_port_names_are_set_before_anything_refers_to_them():
    profile = rename_port(rename_port(router_dhcp(), "ether5", "wan"), "ether1", "lan 1")
    for target in ("7.15.3", "6.49.10"):
        result = generate(profile, TargetContext.offline(target))
        interfaces = result.text.split("BRIDGE / INTERFACE LISTS ----\n")[1]
        # The form /export writes on 7.20.7; quoted like any other value.
        assert interfaces.startswith(
            "# Port names\n/interface ethernet\n"
            "set [ find default-name=ether1 ] name=\"lan 1\"\n"
            "set [ find default-name=ether5 ] name=wan\n"), target
        assert 'add bridge=bridge interface="lan 1"' in interfaces
        assert "add interface=wan list=WAN" in interfaces
        assert "default-name" not in generate(router_dhcp(), TargetContext.offline(target)).text


def test_a_renamed_device_keeps_its_offload_and_its_names():
    # The screenshot of 2026-09-23: ports renamed in Winbox, the table said "Rozhraní typu
    # se neoffloaduje" for every port, because the profile named ports the device lacks.
    names = {"ether1-uplink": "ether1", "ether2-zakaznik1": "ether2", "sfp1-optika": "sfp1"}
    inventory = make_inventory("7.20.7", list(names), default_names=names,
                               switches={"switch1": "EN7523"},
                               port_switch={"ether1-uplink": "switch1",
                                            "ether2-zakaznik1": "switch1"})
    profile = apply_preset(DeviceProfile(), Mode.BRIDGE_AP, ["ether1", "ether2", "sfp1"])
    profile = adopt_device_names(profile, inventory)
    result = generate(profile, TargetContext.from_inventory(inventory))
    offload = {p.port: (p.status.value, p.switch) for p in result.offload.ports}
    assert offload == {"ether1-uplink": ("yes", "switch1"), "ether2-zakaznik1": ("yes", "switch1"),
                       "sfp1-optika": ("no", None)}
    # Already so on the device, still in the script: a reset brings the factory names back.
    assert "set [ find default-name=sfp1 ] name=sfp1-optika" in result.text
    assert result.renames == {}

    # A name the profile changes is known by both names until the script has run.
    renamed = rename_port(profile, "ether1", "uplink")
    result = generate(renamed, TargetContext.from_inventory(inventory))
    assert result.renames == {"ether1-uplink": "uplink"}
    assert result.offload.for_port("uplink").switch == "switch1"
