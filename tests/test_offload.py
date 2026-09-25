from dataclasses import replace

import pytest

from factories import make_inventory
from mqs.capabilities.chips import ChipDatabase, Tri, default_chip_database, device_notes
from mqs.capabilities.matrix import Capabilities, PortKind, classify_port
from mqs.capabilities.offload import OffloadStatus, plan_offload
from mqs.issues import Severity, codes, has_errors
from mqs.messages_cs import format_message
from mqs.model.bridge import BridgeConfig, HwOffloadMode, ProtocolMode
from mqs.model.inventory import InterfaceInfo

RB4011_PORTS = [f"ether{i}" for i in range(1, 11)] + ["sfp-sfpplus1"]


def rb4011(version: str = "7.15.3") -> Capabilities:
    """Two RTL8367 chips: ether1-5 on switch1, ether6-10 on switch2, SFP+ on the CPU."""
    port_switch = {f"ether{i}": "switch1" for i in range(1, 6)}
    port_switch |= {f"ether{i}": "switch2" for i in range(6, 11)}
    inventory = make_inventory(
        version,
        RB4011_PORTS,
        switches={"switch1": "RTL8367", "switch2": "RTL8367"},
        port_switch=port_switch,
        board_name="RB4011iGS+",
    )
    return Capabilities.from_inventory(inventory)


def rb3011(version: str = "7.15.3") -> Capabilities:
    """Two QCA8337 chips: documented as no MSTP and no VLAN filtering with HW offload."""
    ports = [f"ether{i}" for i in range(1, 11)]
    port_switch = {f"ether{i}": "switch1" for i in range(1, 6)}
    port_switch |= {f"ether{i}": "switch2" for i in range(6, 11)}
    return Capabilities.offline(
        version, ports, switches={"switch1": "QCA8337", "switch2": "QCA8337"},
        port_switch=port_switch,
    )


def unknown_chip(version: str = "7.15.3") -> Capabilities:
    return Capabilities.offline(version, ["ether1"], switches={"switch1": "XYZ-9000"},
                                port_switch={"ether1": "switch1"})


def ipq_ppe(version: str = "7.15.3") -> Capabilities:
    return Capabilities.offline(version, ["ether1"], switches={"switch1": "IPQ-PPE"},
                                port_switch={"ether1": "switch1"})


def crs3xx(version: str = "7.15.3") -> Capabilities:
    ports = [f"ether{i}" for i in range(1, 9)]
    return Capabilities.offline(
        version, ports, switches={"switch1": "Marvell-98DX3236"},
        port_switch=dict.fromkeys(ports, "switch1"),
    )


def bridge(**kwargs) -> BridgeConfig:
    kwargs.setdefault("ports", [f"ether{i}" for i in range(2, 11)] + ["sfp-sfpplus1"])
    return BridgeConfig(**kwargs)


def test_rb4011_two_offload_groups():
    plan = plan_offload(bridge(), rb4011())
    assert plan.offered
    assert [(g.switch, g.chip_type, g.ports) for g in plan.groups] == [
        ("switch1", "RTL8367", ("ether2", "ether3", "ether4", "ether5")),
        ("switch2", "RTL8367", ("ether6", "ether7", "ether8", "ether9", "ether10")),
    ]
    assert plan.software_bridge
    assert plan.software_ports == ("sfp-sfpplus1",)
    sfp = plan.for_port("sfp-sfpplus1")
    assert sfp.status == OffloadStatus.NO and sfp.reason == "offload.port_sfp"
    assert sfp.hw is False
    assert plan.for_port("ether2").hw is True
    assert plan.for_port("ether99") is None
    assert {"offload.software_bridge", "offload.mixed_software"} <= codes(plan.issues)
    software = next(i for i in plan.issues if i.code == "offload.software_bridge")
    assert "switch1" in software.message and "switch2" in software.message
    assert not has_errors(plan.issues)


def test_single_chip_bridge_is_fully_offloaded():
    plan = plan_offload(bridge(ports=["ether2", "ether3"]), rb4011())
    assert len(plan.groups) == 1
    assert not plan.software_bridge
    assert plan.issues == ()


def test_mstp_with_offload_is_a_conflict():
    """QCA8337 is documented as "-" for MSTP in the Bridge Hardware Offloading table."""
    plan = plan_offload(bridge(ports=[f"ether{i}" for i in range(2, 11)],
                               protocol_mode=ProtocolMode.MSTP), rb3011())
    assert plan.has_conflict
    conflict = [i for i in plan.issues if i.code == "offload.mstp_conflict"]
    assert [i.params["chip"] for i in conflict] == ["switch1", "switch2"]
    assert all(i.severity == Severity.ERROR and i.field == "bridge.protocol_mode"
               for i in conflict)
    assert "rstp" in conflict[0].message
    assert not any(p.hw for p in plan.ports)  # never a silent hw=yes


def test_mstp_forced_is_still_a_conflict():
    plan = plan_offload(bridge(ports=[f"ether{i}" for i in range(2, 11)],
                               protocol_mode=ProtocolMode.MSTP,
                               hw_offload=HwOffloadMode.FORCE_ON), rb3011())
    assert plan.has_conflict
    assert plan.for_port("ether2").hw is False


def test_mstp_is_offloaded_on_chips_that_support_it():
    """RTL8367 (RB4011) is documented as "+" for MSTP, so there is no conflict."""
    plan = plan_offload(bridge(), rb4011())
    assert not plan.has_conflict
    mstp = plan_offload(bridge(protocol_mode=ProtocolMode.MSTP), rb4011())
    assert not mstp.has_conflict
    assert mstp.for_port("ether2").hw is True


def test_mstp_without_offload_or_on_crs3xx_is_fine():
    off = plan_offload(bridge(protocol_mode=ProtocolMode.MSTP, hw_offload=HwOffloadMode.OFF),
                       rb4011())
    assert not off.has_conflict
    assert all(p.reason == "offload.disabled_by_user" and p.hw is False for p in off.ports)
    assert off.groups == ()

    crs = plan_offload(BridgeConfig(protocol_mode=ProtocolMode.MSTP,
                                    ports=["ether1", "ether2"]), crs3xx())
    assert not crs.has_conflict
    assert all(p.hw for p in crs.ports)


def test_ros_640_does_not_offer_offload():
    plan = plan_offload(bridge(), rb4011("6.40.9"))
    assert not plan.offered
    assert all(p.status == OffloadStatus.NOT_OFFERED and p.hw is None for p in plan.ports)
    assert codes(plan.issues) == {"offload.version_too_old"}
    assert plan_offload(bridge(hw_offload=HwOffloadMode.OFF), rb4011("6.40")).issues == ()


def test_vlan_filtering_depends_on_chip_and_version():
    # RTL8367 got HW vlan-filtering in RouterOS 7.1, so v6 cannot offload it.
    v6 = plan_offload(bridge(ports=["ether2"], vlan_filtering=True), rb4011("6.49.10"))
    assert v6.for_port("ether2").reason == "offload.vlan_filtering_unsupported"
    assert v6.for_port("ether2").hw is False

    v7 = plan_offload(bridge(ports=["ether2"], vlan_filtering=True), rb4011())
    assert v7.for_port("ether2").status == OffloadStatus.YES

    # QCA8337 is documented as "-" for VLAN filtering on any version.
    qca = plan_offload(bridge(ports=["ether2"], vlan_filtering=True), rb3011())
    assert qca.for_port("ether2").reason == "offload.vlan_filtering_unsupported"

    # A chip outside the database stays uncertain instead of claiming support.
    unknown = plan_offload(BridgeConfig(ports=["ether1"], vlan_filtering=True), unknown_chip())
    port = unknown.for_port("ether1")
    assert port.status == OffloadStatus.UNCERTAIN and port.hw is True
    assert "offload.vlan_filtering_uncertain" in codes(unknown.issues)

    crs = plan_offload(BridgeConfig(ports=["ether1"], vlan_filtering=True), crs3xx("6.49"))
    assert crs.for_port("ether1").status == OffloadStatus.YES


def test_use_ip_firewall_disables_offload():
    plan = plan_offload(bridge(ports=["ether2"], use_ip_firewall=True), rb4011())
    assert plan.for_port("ether2").reason == "offload.use_ip_firewall"
    assert "offload.use_ip_firewall" in codes(plan.issues)


def test_force_on():
    inventory = make_inventory(
        ports=["ether1", "sfp1", "wlan1", "ether9"],
        port_types={"wlan1": "wlan"},
        switches={"switch1": "QCA8337"},
        port_switch={"ether1": "switch1", "sfp1": "switch1"},
    )
    caps = Capabilities.from_inventory(inventory)
    plan = plan_offload(BridgeConfig(ports=["ether1", "sfp1", "wlan1", "ether9"],
                                     hw_offload=HwOffloadMode.FORCE_ON), caps)
    assert plan.for_port("ether1").reason == "offload.ok"
    sfp = plan.for_port("sfp1")
    assert sfp.reason == "offload.forced" and sfp.hw is True
    assert sfp.params["original"] == "offload.port_sfp"
    assert plan.for_port("wlan1").reason == "offload.port_wireless"
    assert plan.for_port("ether9").reason == "offload.port_no_switch"
    forced = next(i for i in plan.issues if i.code == "offload.forced")
    assert forced.params["port"] == "sfp1"


def test_unknown_chip_and_family_prefix():
    caps = Capabilities.offline("7.15", ["ether1"], switches={"switch1": "XYZ-9000"},
                                port_switch={"ether1": "switch1"})
    assert plan_offload(BridgeConfig(ports=["ether1"]), caps).for_port("ether1").reason == \
        "offload.ok_unknown_chip"

    db = default_chip_database()
    assert db.lookup("Marvell-98DX9999").mstp == Tri.YES
    assert db.lookup("RTL8367").known
    assert not db.lookup("XYZ-9000").known


def test_chip_lookup_ignores_punctuation_and_case():
    """The CLI prints "Atheros-8327", the documentation table writes "Atheros8327"."""
    db = default_chip_database()
    for spelling in ("Atheros-8327", "Atheros8327", "atheros8327"):
        chip = db.lookup(spelling)
        assert chip.known and chip.mstp == Tri.NO
    assert db.lookup("Marvell 98DX3236").vlan_filtering(7) == Tri.YES


def test_the_mt7621_of_a_routeros_6_hex_s_is_known():
    """RB760iGS on 6.49.10 reports its switch as "MediaTek-MT7621" (2026-09-25); unknown,
    it made vlan-filtering "uncertain" on every port and refused MSTP."""
    db = default_chip_database()
    reported, documented = db.lookup("MediaTek-MT7621"), db.lookup("MT7621")
    assert reported.known
    assert replace(reported, type="MT7621") == documented
    caps = Capabilities.offline("6.49.10", ["ether2", "ether3"],
                                switches={"switch1": "MediaTek-MT7621"},
                                port_switch={"ether2": "switch1", "ether3": "switch1"})
    plain = plan_offload(BridgeConfig(ports=["ether2", "ether3"]), caps)
    assert plain.for_port("ether2").reason == "offload.ok"
    vlan = plan_offload(BridgeConfig(ports=["ether2", "ether3"], vlan_filtering=True), caps)
    assert vlan.for_port("ether2").reason == "offload.vlan_filtering_unsupported"
    assert not [i for i in vlan.issues if i.code == "offload.vlan_filtering_uncertain"]


@pytest.mark.parametrize(("version", "mode", "offloaded"), [
    ("6.49.10", ProtocolMode.NONE, True),
    ("6.49.10", ProtocolMode.STP, False),
    ("6.49.10", ProtocolMode.RSTP, False),
    ("7.15", ProtocolMode.RSTP, True),  # documented "+", not measured: left as it was
])
def test_mt7621_on_routeros_6_offloads_no_spanning_tree(version, mode, offloaded):
    """RB760iGS, 6.49.10 (2026-09-25): H on every port with protocol-mode none, on none with
    stp or rstp; hw=yes there only made the checklist fail."""
    ports = ["ether2", "ether3"]
    caps = Capabilities.offline(version, ports, switches={"switch1": "MediaTek-MT7621"},
                                port_switch=dict.fromkeys(ports, "switch1"))
    plan = plan_offload(BridgeConfig(ports=ports, protocol_mode=mode), caps)
    assert [p.hw for p in plan.ports] == [offloaded, offloaded]
    warned = [i for i in plan.issues if i.code == "offload.stp_no_offload"]
    assert len(warned) == (0 if offloaded else 1)
    if warned:
        assert plan.for_port("ether2").reason == "offload.stp_unsupported"
        assert "protocol-mode=" + mode.value in warned[0].message


def test_an_uncertain_chip_is_reported_once_not_for_every_port():
    ports = ["ether2", "ether3", "ether4"]
    caps = Capabilities.offline("7.15", ports, switches={"switch1": "XYZ-9000"},
                                port_switch=dict.fromkeys(ports, "switch1"))
    plan = plan_offload(BridgeConfig(ports=ports, vlan_filtering=True), caps)
    assert [i.code for i in plan.issues].count("offload.vlan_filtering_uncertain") == 1


def test_documented_chips_are_all_known():
    """Every chip named in MikroTik's switch chip documentation must be in the database."""
    db = default_chip_database()
    documented = [
        # Switch Chip Features (non-CRS devices)
        "QCA8337", "Atheros8327", "Atheros8316", "Atheros8227", "Atheros7240", "IPQ-PPE",
        "ICPlus175D", "ICPlus175C", "ICPlus178C", "MT7621", "MT7531", "EN7523", "RTL8367",
        "88E6393X", "88E6191X", "88E6190", "Marvell-98PX1012",
        # CRS1xx / CRS2xx series
        "QCA-8511", "QCA-8519", "QCA-8513L",
        # Marvell Prestera (CRS3xx, CRS5xx, CCR2116, CCR2216, RDS2216)
        "Marvell-98DX224S", "Marvell-98DX226S", "Marvell-98DX2528", "Marvell-98DX3236",
        "Marvell-98DX3255", "Marvell-98DX3257", "Marvell-98DX4310", "Marvell-98DX7335",
        "Marvell-98DX8208", "Marvell-98DX8212", "Marvell-98DX8216", "Marvell-98DX8332",
        "Marvell-98DX8525", "Marvell-98CX8410",
    ]
    assert [c for c in documented if not db.lookup(c).known] == []


def test_new_prestera_parts_fall_back_to_the_family():
    db = default_chip_database()
    for chip in ("Marvell-98DX9999", "Marvell-98CX9999"):
        assert db.lookup(chip).mstp == Tri.YES
        assert db.lookup(chip).vlan_filtering(7) == Tri.YES


def test_ipq_ppe_offload_is_not_offered_but_can_be_forced():
    """MikroTik recommends a software bridge with RSTP on IPQ-PPE (hAP ax2/ax3, cAP ax)."""
    plan = plan_offload(BridgeConfig(ports=["ether1"]), ipq_ppe())
    port = plan.for_port("ether1")
    assert port.reason == "offload.chip_unsupported" and port.hw is False

    forced = plan_offload(BridgeConfig(ports=["ether1"], hw_offload=HwOffloadMode.FORCE_ON),
                          ipq_ppe())
    assert forced.for_port("ether1").hw is True


def test_port_missing_from_capabilities():
    plan = plan_offload(BridgeConfig(ports=["ghost1"]), rb4011())
    assert plan.for_port("ghost1").reason == "offload.port_other"


def test_all_issue_messages_format():
    plans = [
        plan_offload(bridge(), rb4011()),
        plan_offload(bridge(protocol_mode=ProtocolMode.MSTP), rb4011()),
        plan_offload(bridge(), rb4011("6.40")),
        plan_offload(bridge(vlan_filtering=True, use_ip_firewall=True), rb4011()),
        plan_offload(bridge(vlan_filtering=True), rb4011()),
        plan_offload(bridge(hw_offload=HwOffloadMode.FORCE_ON), rb4011()),
        plan_offload(bridge(protocol_mode=ProtocolMode.MSTP), rb3011()),
        plan_offload(BridgeConfig(ports=["ether1"]), ipq_ppe()),
    ]
    for plan in plans:
        for issue in plan.issues:
            assert "{" not in issue.message, issue.code
        for port in plan.ports:
            assert "{" not in format_message(port.reason, port.params), port.reason


@pytest.mark.parametrize(
    ("iface", "kind"),
    [
        (InterfaceInfo(name="ether1", type="ether"), PortKind.ETHERNET),
        (InterfaceInfo(name="sfp-sfpplus1", type="ether"), PortKind.SFP),
        (InterfaceInfo(name="qsfp28-1-1", type="ether"), PortKind.SFP),
        (InterfaceInfo(name="combo1", type="ether"), PortKind.SFP),
        (InterfaceInfo(name="uplink", type="ether", default_name="sfp1"), PortKind.SFP),
        (InterfaceInfo(name="wlan1", type="wlan"), PortKind.WIRELESS),
        (InterfaceInfo(name="wifi1", type="wifi"), PortKind.WIRELESS),
        (InterfaceInfo(name="bond1", type="bond"), PortKind.BONDING),
        (InterfaceInfo(name="vlan10", type="vlan"), PortKind.VLAN),
        (InterfaceInfo(name="bridge", type="bridge"), PortKind.OTHER),
    ],
)
def test_classify_port(iface, kind):
    assert classify_port(iface) == kind


def test_capabilities_from_inventory_and_offline():
    caps = rb4011()
    assert caps.board_name == "RB4011iGS+"
    assert caps.chips == {"switch1": "RTL8367", "switch2": "RTL8367"}
    assert caps.port("ether7").switch == "switch2"
    assert caps.port("ether7").chip_type == "RTL8367"
    assert caps.port("sfp-sfpplus1").chip_type is None

    offline = Capabilities.offline("6.49", ["ether1", "wlan1"], port_types={"wlan1": "wlan"})
    assert offline.version.major == 6
    assert offline.port("wlan1").kind == PortKind.WIRELESS
    assert offline.port("ether1").switch is None


def test_chip_database_helpers():
    assert Tri.of(True) == Tri.YES and Tri.of(False) == Tri.NO and Tri.of("unknown") == Tri.UNKNOWN
    empty = ChipDatabase.from_mapping({})
    assert not empty.lookup("RTL8367").known
    chip = default_chip_database().lookup("QCA8337")
    assert chip.vlan_filtering(6) == Tri.NO
    assert chip.vlan_filtering(7) == Tri.NO
    assert chip.mstp == Tri.NO
    assert device_notes("RB4011iGS+")
    assert device_notes("unknown-board") == []


def test_device_notes_match_the_longest_prefix():
    # /system resource print reports suffixes the documentation does not use.
    assert "RTL8367" in device_notes("RB4011iGS+5HacQ2HnD")[0]

    # RB750, RB750G, RB750Gr2 and RB750Gr3 are four different switch chips, so a
    # first-match prefix rule would answer with the wrong one.
    assert "Atheros7240" in device_notes("RB750")[0]
    assert "Atheros8316" in device_notes("RB750G")[0]
    assert "QCA8337" in device_notes("RB750Gr2")[0]
    assert "MT7621" in device_notes("RB750Gr3")[0]
    assert "Atheros8227" in device_notes("RB750UPr2")[0]

    # RB1100AHx4 must not fall back to the RB1100 entry.
    assert "RTL8367" in device_notes("RB1100AHx4")[0]
    assert "Atheros8316" in device_notes("RB1100")[0]
    assert "Atheros8327" in device_notes("RB1100AHx2")[0]

    assert device_notes("") == []
    assert device_notes("Generic-Board") == []


def test_device_notes_prefer_model_over_board_name():
    """`board-name` is a marketing name MikroTik reuses across hardware revisions.

    Verified on a device 2026-09-16: a hEX S reports `board-name=hEX S` both as the
    documented RB760iGS (MT7621) and as model E60iUGS (EN7523), so the marketing name
    must never be what decides which switch chip a board has.
    """
    assert "MT7621" in device_notes("RB760iGS", "hEX S")[0]
    assert "EN7523" in device_notes("E60iUGS", "hEX S")[0]
    assert device_notes("hEX S") == []
    assert device_notes("", "RB4011iGS+")  # model unknown -> board-name still tried
