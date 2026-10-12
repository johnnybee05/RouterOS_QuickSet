"""The sources that reach out: neighbour tables, registration tables, SNMP, a port scan.

Two things matter in all of them. They must survive a device that answers differently or
not at all — the whole reason for having several sources is that one can fail — and they
must not overstate what they found.
"""

from __future__ import annotations

import time
from ipaddress import IPv4Address

import pytest

from fake_transport import FakeTransport
from mqs.discover import snmp
from mqs.discover.candidates import Confidence, Source, from_sighting
from mqs.discover.portscan import (
    OpenPort,
    ScanLimits,
    TooManyAddresses,
    addresses,
    scan,
    to_sightings,
)
from mqs.discover.sources import (
    crawl,
    from_mndp,
    read_device,
    read_neighbors,
    read_registrations,
    seen_from,
)
from mqs.model.version import RosVersion
from mqs.transport.mndp import DiscoveredDevice

V = RosVersion.parse


# ------------------------------------------------------------------ MNDP


def test_an_mndp_announcement_becomes_a_sighting():
    device = DiscoveredDevice(mac="AA:BB:CC:00:00:01", identity="sxt-01",
                              version="7.15.3 (stable)", platform="MikroTik",
                              board="SXTsq 5 nD", ipv4=IPv4Address("192.0.2.5"),
                              local_interface="eth0", interface_name="ether1")
    sighting = from_mndp(device)
    assert sighting.source is Source.MNDP and sighting.mikrotik
    assert sighting.address == "192.0.2.5" and sighting.found_at == "eth0"
    assert sighting.note == "from ether1"


def test_an_announcement_without_an_address_falls_back_to_where_it_came_from():
    device = DiscoveredDevice(mac="AA:BB:CC:00:00:01", source_ip="192.0.2.9")
    assert from_mndp(device).address == "192.0.2.9"


# ------------------------------------------------------------------ neighbour tables


def test_a_neighbour_table_is_read_into_sightings():
    fake = FakeTransport({"/ip/neighbor/print": [
        {"address": "192.0.2.5", "mac-address": "AA:BB:CC:00:00:01", "identity": "sxt-01",
         "platform": "MikroTik", "version": "7.15.3", "board": "SXTsq 5 nD",
         "interface": "ether1"},
    ]})
    found = read_neighbors(fake, found_at="core-1")
    assert len(found) == 1
    assert found[0].found_at == "core-1" and found[0].via_interface == "ether1"
    assert found[0].mikrotik
    assert from_sighting(found[0]).confidence is Confidence.PROBABLE


def test_a_neighbour_of_another_vendor_is_kept_and_not_claimed():
    fake = FakeTransport({"/ip/neighbor/print": [
        {"address": "192.0.2.6", "mac-address": "AA:BB:CC:00:00:02",
         "identity": "switch", "platform": "Cisco"}]})
    found = read_neighbors(fake, found_at="core-1")
    assert len(found) == 1 and not found[0].mikrotik


def test_the_other_spellings_of_the_neighbour_fields_are_read():
    """v6 and v7 do not agree on all of them, and neither does `print terse`."""
    fake = FakeTransport({"/ip/neighbor/print": [
        {"address4": "192.0.2.7", "mac": "AA:BB:CC:00:00:03", "name": "lhg-02",
         "software-version": "6.49.10", "model": "LHG 5", "platform": "MikroTik",
         "local-interface": "wlan1"}]})
    found = read_neighbors(fake, found_at="core-1")[0]
    assert found.address == "192.0.2.7" and found.identity == "lhg-02"
    assert found.version == "6.49.10" and found.board == "LHG 5"
    assert found.via_interface == "wlan1"


def test_only_the_first_interface_of_a_list_is_taken():
    fake = FakeTransport({"/ip/neighbor/print": [
        {"address": "192.0.2.8", "mac-address": "AA:BB:CC:00:00:04",
         "interface": "ether1,ether2", "platform": "MikroTik"}]})
    assert read_neighbors(fake, found_at="c")[0].via_interface == "ether1"


def test_a_row_with_neither_address_nor_mac_is_skipped():
    fake = FakeTransport({"/ip/neighbor/print": [{"identity": "ghost"}]})
    assert read_neighbors(fake, found_at="c") == []


def test_a_device_without_the_menu_simply_says_nothing():
    assert read_neighbors(FakeTransport({}), found_at="c") == []


# ------------------------------------------------------------------ registration tables


def test_an_ap_lists_its_stations_with_the_radio_each_is_on():
    fake = FakeTransport({"/interface/wireless/registration-table/print": [
        {"interface": "wlan1", "mac-address": "AA:BB:CC:00:00:11"},
        {"interface": "wlan2", "mac-address": "AA:BB:CC:00:00:12"},
    ]})
    found = read_registrations(fake, V("7.15.3"), found_at="ap-1")
    assert [s.via_interface for s in found] == ["wlan1", "wlan2"]
    assert all(s.source is Source.REGISTRATION and s.found_at == "ap-1" for s in found)
    assert all(s.mikrotik for s in found)


def test_the_new_driver_table_is_read_too():
    fake = FakeTransport({"/interface/wifi/registration-table/print": [
        {"interface": "wifi1", "mac-address": "AA:BB:CC:00:00:21", "identity": "cpe-9"}]})
    found = read_registrations(fake, V("7.16.1"), found_at="ap-2")
    assert len(found) == 1 and found[0].identity == "cpe-9"


def test_v6_is_not_asked_about_the_new_driver_table():
    fake = FakeTransport({"/interface/wireless/registration-table/print": []})
    read_registrations(fake, V("6.49.10"), found_at="ap-1")
    assert "/interface/wifi/registration-table/print" not in [p for p, _ in fake.calls]


def test_a_sixty_gigahertz_link_names_its_one_peer():
    """No registration table for w60g — one peer per interface, in `remote-address`."""
    fake = FakeTransport({"/interface/w60g/print": [
        {"name": "wlan60-1", "remote-address": "AA:BB:CC:00:00:31"},
        {"name": "wlan60-2"},  # nothing on the far end
    ]})
    found = read_registrations(fake, V("7.15.3"), found_at="link-a")
    assert len(found) == 1
    assert found[0].macs == ["AA:BB:CC:00:00:31"] and found[0].via_interface == "wlan60-1"


def test_a_station_has_no_table_and_that_is_not_a_failure():
    assert read_registrations(FakeTransport({}), V("7.15.3"), found_at="cpe-1") == []


def test_one_device_gives_up_both_kinds_at_once():
    fake = FakeTransport({
        "/ip/neighbor/print": [{"address": "192.0.2.5", "mac-address": "AA:BB:CC:00:00:01",
                                "platform": "MikroTik"}],
        "/interface/wireless/registration-table/print": [
            {"interface": "wlan1", "mac-address": "AA:BB:CC:00:00:11"}],
    })
    found = read_device(fake, V("7.15.3"), found_at="ap-1")
    assert {s.source for s in found} == {Source.NEIGHBOR, Source.REGISTRATION}


def lab_switch() -> FakeTransport:
    """What test-bridge listed on 2026-10-12: everything on the port the PC came in by."""
    return FakeTransport({"/ip/neighbor/print": [
        {"address": "192.168.55.1", "mac-address": "08:55:31:F7:00:1B",
         "identity": "hAP", "platform": "MikroTik", "interface": "ether1-uplink,bridge1"},
        {"address": "192.168.55.55", "mac-address": "BC:FC:E7:C9:5D:A1",
         "interface": "ether1-uplink,bridge1"},
        {"address": "10.0.0.2", "mac-address": "AA:BB:CC:00:00:05", "identity": "behind",
         "platform": "MikroTik", "interface": "ether2"},
    ]})


def test_what_is_seen_on_the_session_port_is_not_behind_the_device():
    found = seen_from(lab_switch(), V("7.24.4"), found_at="test-bridge",
                      session_port="ether1-uplink")
    assert {s.identity: s.upstream for s in found if s.identity} == {
        "hAP": True, "behind": False}


def test_this_pc_is_left_out_of_what_a_device_sees():
    found = seen_from(lab_switch(), V("7.24.4"), found_at="test-bridge",
                      session_port="ether1-uplink", own_addresses=["192.168.55.55"])
    assert "192.168.55.55" not in [s.address for s in found]


def test_without_a_session_port_nothing_is_taken_as_behind():
    found = seen_from(lab_switch(), V("7.24.4"), found_at="test-bridge", session_port=None)
    assert found and all(s.upstream for s in found)


def test_a_crawl_carries_on_past_a_device_that_says_nothing():
    """One source failing is not the crawl failing."""
    talkative = FakeTransport({"/ip/neighbor/print": [
        {"address": "192.0.2.5", "mac-address": "AA:BB:CC:00:00:01",
         "platform": "MikroTik"}]})
    silent = FakeTransport({})
    seen: list[tuple[str, int]] = []
    found = crawl([("quiet", silent, V("7.15.3")), ("core", talkative, V("7.15.3"))],
                  on_source=lambda where, count: seen.append((where, count)))
    assert len(found) == 1
    assert seen == [("quiet", 0), ("core", 1)]


# ------------------------------------------------------------------ SNMP


def _response(object_id: str = "1.3.6.1.4.1.14988.1", descr: str = "RouterOS RB760iGS",
              name: str = "sxt-01") -> bytes:
    def tlv(tag: int, body: bytes) -> bytes:
        return bytes([tag]) + snmp._length(len(body)) + body

    def binding(oid: str, tag: int, value: bytes) -> bytes:
        return tlv(0x30, snmp.encode_oid(oid) + tlv(tag, value))

    bindings = (binding(snmp.SYS_DESCR, 0x04, descr.encode())
                + binding(snmp.SYS_OBJECT_ID, 0x06, snmp.encode_oid(object_id)[2:])
                + binding(snmp.SYS_NAME, 0x04, name.encode()))
    pdu = (tlv(0x02, b"\x01") + tlv(0x02, b"\x00") + tlv(0x02, b"\x00")
           + tlv(0x30, bindings))
    return tlv(0x30, tlv(0x02, b"\x01") + tlv(0x04, b"public") + tlv(0xA2, pdu))


@pytest.mark.parametrize("oid", ["1.3.6.1.4.1.14988.1", "1.3.6.1.2.1.1.2.0",
                                 "0.0", "2.999.1"])
def test_an_oid_survives_the_round_trip(oid):
    assert snmp.decode_oid(snmp.encode_oid(oid)[2:]) == oid


def test_a_long_oid_arc_is_encoded_in_seven_bit_groups():
    assert snmp.decode_oid(snmp.encode_oid("1.3.6.1.4.1.14988.1.1.4")[2:]) == \
        "1.3.6.1.4.1.14988.1.1.4"


def test_a_get_asks_for_all_three_in_one_packet():
    packet = snmp.build_get("public", snmp.WANTED)
    assert packet[0] == 0x30 and b"public" in packet
    for oid in snmp.WANTED:
        assert snmp.encode_oid(oid) in packet


def test_the_enterprise_oid_settles_it():
    reply = snmp.query("192.0.2.5", "public", send=lambda *a: _response())
    assert reply is not None and reply.mikrotik
    assert reply.sys_name == "sxt-01" and reply.sys_descr == "RouterOS RB760iGS"
    assert snmp.to_sighting(reply).mikrotik


def test_something_else_answering_snmp_is_not_claimed():
    reply = snmp.query("192.0.2.6", "public",
                       send=lambda *a: _response("1.3.6.1.4.1.9.1.1", "Cisco IOS", "sw1"))
    assert reply is not None and not reply.mikrotik
    sighting = snmp.to_sighting(reply)
    assert not sighting.mikrotik
    # Still kept: "answers SNMP, is not a MikroTik" is a useful row.
    assert from_sighting(sighting).confidence is Confidence.UNKNOWN


def test_nothing_answering_is_not_an_error():
    def refuse(*_args: object) -> bytes:
        raise OSError("timed out")

    assert snmp.query("192.0.2.7", "public", send=refuse) is None


def test_a_reply_that_is_not_snmp_is_not_an_error_either():
    assert snmp.query("192.0.2.8", "public", send=lambda *a: b"hello") is None


def test_a_truncated_packet_is_rejected_rather_than_read_past():
    with pytest.raises(snmp.SnmpError):
        snmp.parse_response(_response()[:10])


# ------------------------------------------------------------------ the port scan


def test_a_subnet_becomes_its_hosts():
    hosts = addresses(["192.0.2.0/30"])
    assert [str(h) for h in hosts] == ["192.0.2.1", "192.0.2.2"]


def test_a_single_address_means_that_address():
    assert [str(h) for h in addresses(["192.0.2.5"])] == ["192.0.2.5"]
    assert [str(h) for h in addresses(["192.0.2.5/32"])] == ["192.0.2.5"]


def test_overlapping_subnets_are_not_scanned_twice():
    assert len(addresses(["192.0.2.0/30", "192.0.2.0/29"])) == 6


def test_a_typo_that_would_scan_the_internet_is_refused():
    """The guard against a wrong prefix turning a survey of one office into something
    else entirely — and refused from the prefix, not from a list built out of it.

    Enumerating a /8 to discover it is too big costs sixteen million addresses in memory
    and a window that stops answering while it happens, so this is timed.
    """
    started = time.monotonic()
    with pytest.raises(TooManyAddresses, match="more than the limit"):
        addresses(["10.0.0.0/8"])
    assert time.monotonic() - started < 1.0


def test_several_subnets_that_add_up_to_too_much_are_refused_too():
    with pytest.raises(TooManyAddresses):
        addresses([f"10.0.{block}.0/24" for block in range(32)], limit=1000)


def test_something_that_is_not_a_subnet_is_refused():
    with pytest.raises(TooManyAddresses):
        addresses(["not a subnet"])


def test_a_scan_reports_the_addresses_that_answered():
    def knock(address: str, port: int, timeout: float) -> bool:
        return address == "192.0.2.2" and port == 8729

    found = scan(["192.0.2.0/29"], knock=knock, sleep=lambda s: None)
    assert found == [OpenPort("192.0.2.2", 8729)]


def test_an_address_is_dropped_as_soon_as_one_port_answers():
    """The point is somewhere to try logging in; a second open port adds nothing."""
    asked: list[tuple[str, int]] = []

    def knock(address: str, port: int, timeout: float) -> bool:
        asked.append((address, port))
        return True

    scan(["192.0.2.1"], knock=knock, sleep=lambda s: None)
    assert asked == [("192.0.2.1", 8729)]


def test_the_scan_is_paced():
    """Ten a second means a tenth of a second apart, and the waits do not pile up: a
    pacer that added to its own backlog would crawl to a halt over a /24."""
    slept: list[float] = []
    clock = {"t": 0.0}

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["t"] += seconds

    scan(["192.0.2.0/29"], ScanLimits(rate=10.0, workers=1), knock=lambda *a: False,
         sleep=sleep, now=lambda: clock["t"])
    assert len(slept) == 5 and all(abs(s - 0.1) < 1e-9 for s in slept)


def test_pacing_can_be_turned_off():
    slept: list[float] = []
    scan(["192.0.2.0/29"], ScanLimits(rate=0.0, workers=1),
         knock=lambda *a: False, sleep=slept.append, now=lambda: 0.0)
    assert slept == []


def test_a_scan_can_be_cancelled():
    knocks: list[str] = []
    scan(["192.0.2.0/24"], ScanLimits(workers=1, rate=0.0),
         knock=lambda a, p, t: knocks.append(a) or False,
         cancelled=lambda: len(knocks) >= 3, sleep=lambda s: None)
    assert len(knocks) <= 4  # the one in flight may finish


def test_progress_is_reported():
    seen: list[tuple[int, int]] = []
    scan(["192.0.2.0/30"], ScanLimits(rate=0.0), knock=lambda *a: False,
         on_progress=lambda done, total: seen.append((done, total)), sleep=lambda s: None)
    assert seen == [(1, 2), (2, 2)]


def test_an_open_port_proves_nothing_about_the_device():
    sightings = to_sightings([OpenPort("192.0.2.2", 8291)], found_at="192.0.2.0/24")
    assert sightings[0].note == "port 8291" and not sightings[0].mikrotik
    assert from_sighting(sightings[0]).confidence is Confidence.UNKNOWN
