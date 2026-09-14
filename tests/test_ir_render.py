import pytest

from factories import make_inventory
from mqs.generator.context import TargetContext
from mqs.generator.features import FEATURES, is_supported_version, version_supports
from mqs.generator.ir import (
    Command,
    Comment,
    Find,
    RawLine,
    Script,
    SectionKey,
    cmd,
    normalize_path,
    normalize_value,
)
from mqs.generator.render import quote, render_command, render_inline, render_script
from mqs.model.inventory import Ipv6PackageState, PackageInfo
from mqs.model.version import RosVersion


def inventory_v6_with_ipv6():
    return make_inventory("6.49.10", packages=[PackageInfo(name="ipv6")])


def test_normalize_path():
    assert normalize_path("/ip/firewall/filter") == "/ip firewall filter"
    assert normalize_path("  /ip   service ") == "/ip service"
    with pytest.raises(ValueError):
        normalize_path("/")


def test_normalize_value():
    assert normalize_value(True) == "yes"
    assert normalize_value(False) == "no"
    assert normalize_value(122) == "122"
    assert normalize_value(["a", "b"]) == "a,b"


def test_cmd_builder_and_accessors():
    c = cmd(
        "/ip/firewall/filter", "add", chain="forward", connection_state=["established", "related"]
    )
    assert c.path == "/ip firewall filter"
    assert c.api_path == "/ip/firewall/filter"
    assert c.args == (("chain", "forward"), ("connection-state", "established,related"))
    assert c.arg("chain") == "forward"
    assert c.arg("missing") is None
    assert c.has_arg("connection-state")

    updated = c.with_args(chain="input", action="accept")
    assert updated.args == (("chain", "input"), ("connection-state", "established,related"),
                            ("action", "accept"))
    assert updated.without_args("chain").args[0] == ("connection-state", "established,related")
    raw_keys = cmd("/snmp", "set", args={"trap-version": 2})
    assert raw_keys.args == (("trap-version", "2"),)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("yes", "yes"),
        ("::/0", "::/0"),
        ("!dynamic", "!dynamic"),
        ("192.168.200.200,8.8.8.8", "192.168.200.200,8.8.8.8"),
        ("Europe/Prague", "Europe/Prague"),
        ("", '""'),
        ("two words", '"two words"'),
        ('say "hi"', '"say \\"hi\\""'),
        ("a$b?c\\d", '"a\\$b\\?c\\\\d"'),
    ],
)
def test_quote(value, expected):
    assert quote(value) == expected


def test_render_command_selectors():
    assert render_command(cmd("/snmp community", "set", selector=Find.by(default=True),
                              disabled=True)) == "set [ find default=yes ] disabled=yes"
    assert render_command(cmd("/system logging", "set", selector=0, action="disk")) == \
        "set 0 action=disk"
    assert render_command(cmd("/ip service", "set", selector="ssh", port=122)) == \
        "set ssh port=122"
    assert render_command(cmd("/x", "remove", selector=Find())) == "remove [ find ]"
    raw = Find(raw='where topics~"info"')
    assert render_command(cmd("/system logging", "set", selector=raw, action="disk")) == \
        'set [ find where topics~"info" ] action=disk'
    assert render_command(cmd("/ip dns", "set", selector=Find.by(name="my name"))) == \
        'set [ find name="my name" ]'


def test_find_get():
    f = Find.by(interface_list="WAN")
    assert f.get("interface-list") == "WAN"
    assert f.get("x") is None


def test_render_inline():
    assert render_inline(cmd("/ip dns", "set", servers=["1.1.1.1"])) == \
        "/ip dns set servers=1.1.1.1"
    assert render_inline(RawLine("set foo=bar", "/ip dns")) == "/ip dns set foo=bar"
    assert render_inline(RawLine(":delay 5s")) == ":delay 5s"


def test_script_sections_sorted_and_rendered():
    script = Script()
    script.section(SectionKey.SERVICES).add(cmd("/ip service", "set", selector="ssh", port=122))
    script.section(SectionKey.IDENTITY).add(
        Comment("identity"), cmd("/system identity", "set", name="R1")
    )
    script.section(SectionKey.SYSTEM).add(
        cmd("/ip dns", "set", servers="1.1.1.1"),
        RawLine("set allow-remote-requests=no", "/ip dns"),
        RawLine("set foo=bar", "/ip proxy"),
        cmd("/ip dns", "set", cache_size=4096),
    )
    script.section(SectionKey.FIREWALL)  # empty sections are skipped
    assert [s.key for s in script.sections] == [
        SectionKey.IDENTITY, SectionKey.FIREWALL, SectionKey.SYSTEM, SectionKey.SERVICES
    ]
    assert script.section(SectionKey.IDENTITY) is script.sections[0]

    text = render_script(script, header="generated\n")
    assert text == (
        "# generated\n"
        "# ---- IDENTITY ----\n"
        "# identity\n"
        "/system identity\n"
        "set name=R1\n"
        "# ---- DNS / NTP / CLOCK / SNMP / DISCOVERY / IPV6 / LOGGING ----\n"
        "/ip dns\n"
        "set servers=1.1.1.1\n"
        "set allow-remote-requests=no\n"
        "/ip proxy\n"
        "set foo=bar\n"
        "/ip dns\n"
        "set cache-size=4096\n"
        "# ---- IP SERVICES ----\n"
        "/ip service\n"
        "set ssh port=122\n"
    )
    assert len(list(script.commands())) == 4
    assert script.find("/ip/dns", "set", servers="1.1.1.1")[0].arg("servers") == "1.1.1.1"
    assert script.find("/ip dns", "add") == []
    assert script.find("/ip dns", cache_size="4096")


def test_command_is_hashable_and_frozen():
    c = Command("/ip dns", "set")
    assert {c, Command("/ip/dns", "set")} == {c}


def test_features_table_consistency():
    for key, feature in FEATURES.items():
        assert key == feature.key
        assert feature.min_v6 is None or feature.min_v6[0] == 6
        assert feature.min_v7 is None or feature.min_v7[0] == 7


@pytest.mark.parametrize(
    ("key", "version", "expected"),
    [
        ("interface_list", "6.40", False),
        ("interface_list", "6.41", True),
        ("interface_list", "7.1", True),
        ("bridge_port_hw", "6.40.9", False),
        ("ntp_servers_menu", "6.49.10", False),
        ("ntp_servers_menu", "7.15", True),
        ("ntp_server_dns_names", "7.15", False),
        ("fasttrack_hw_offload", "6.49", False),
        ("fasttrack_hw_offload", "7.15", True),
        ("interface_list", "5.26", False),
    ],
)
def test_version_supports(key, version, expected):
    assert version_supports(key, RosVersion.parse(version)) is expected


def test_is_supported_version():
    assert not is_supported_version(RosVersion.parse("6.39.3"))
    assert is_supported_version(RosVersion.parse("6.40"))
    assert is_supported_version(RosVersion.parse("7.15"))
    assert not is_supported_version(RosVersion.parse("8.0"))


def test_context_runtime_probe_and_consulted():
    offline = TargetContext.offline("7.15")
    assert offline.major == 7 and not offline.online
    assert offline.ipv6_package == Ipv6PackageState.BUILTIN
    assert not offline.has("fasttrack_hw_offload")  # probe missing
    assert offline.has("interface_list")
    assert {f.key for f in offline.unverified_consulted()} == {
        "fasttrack_hw_offload", "interface_list"
    }
    probed = TargetContext.offline("7.15", probes={"fasttrack_hw_offload": True})
    assert probed.has("fasttrack_hw_offload")

    v6 = TargetContext.offline(RosVersion.parse("6.49"), ipv6_package=Ipv6PackageState.MISSING)
    assert v6.ipv6_package == Ipv6PackageState.MISSING

    online = TargetContext.from_inventory(inventory_v6_with_ipv6())
    assert online.online
    assert online.ipv6_package == Ipv6PackageState.ENABLED
