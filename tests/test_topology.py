"""The tree, and the update order drawn from it.

The rule being tested, over and over: never update a device before the ones behind it.
Reboot an AP first and every station on it is gone for two minutes — and a station that
then fails has failed out of reach.
"""

from __future__ import annotations

from factories import make_inventory, make_radios
from mqs.discover.candidates import Candidate, Sighting, Source, confirmed, from_sighting
from mqs.discover.topology import (
    Link,
    build,
    describe,
    links_from_sightings,
    update_order,
)
from mqs.model.inventory import PackageInfo

MB = 1024 * 1024


def device(identity: str, *, serial: str = "", mode: str = "station-bridge",
           address: str = "", sightings: tuple[Sighting, ...] = ()) -> Candidate:
    inventory = make_inventory(
        "7.15.3", architecture="arm", model="RBSXTsq5nD", free_hdd_space=256 * MB,
        radios=make_radios(("wlan1", "wireless", mode)),
        packages=[PackageInfo(name="routeros", version="7.15.3"),
                  PackageInfo(name="wireless", version="7.15.3")])
    board = inventory.routerboard.model_copy(
        update={"serial_number": serial or identity.upper()})
    inventory = inventory.model_copy(update={"identity": identity, "routerboard": board})
    base = Candidate(addresses=[address] if address else [], sightings=list(sightings))
    return confirmed(base, inventory)


def unconfirmed(identity: str) -> Candidate:
    return from_sighting(Sighting(source=Source.MNDP, identity=identity,
                                  macs=["AA:BB:CC:00:00:09"]))


def registered(on: str, interface: str = "wlan1") -> Sighting:
    """How a station arrives: its AP's registration table named it."""
    return Sighting(source=Source.REGISTRATION, found_at=on, via_interface=interface)


# ------------------------------------------------------------------ drawing the tree


def test_an_ap_listing_a_station_is_an_edge():
    ap = device("ap-1", mode="ap-bridge")
    station = device("cpe-1", sightings=(registered("ap-1"),))
    links = links_from_sightings([ap, station])
    assert links == [Link(ap.key, station.key, "wlan1", Source.REGISTRATION)]


def test_a_device_is_matched_by_whatever_name_it_was_reported_under():
    ap = device("ap-1", address="192.0.2.1")
    by_address = device("cpe-1", sightings=(registered("192.0.2.1"),))
    by_serial = device("cpe-2", sightings=(registered("AP-1"),))
    links = links_from_sightings([ap, by_address, by_serial])
    assert {link.child for link in links} == {by_address.key, by_serial.key}


def neighbour(on: str) -> Sighting:
    return Sighting(source=Source.NEIGHBOR, found_at=on, via_interface="ether1")


def test_two_routers_listing_each_other_as_neighbours_are_not_a_loop():
    """Two routers on one switch: neither is behind the other, so there is no edge."""
    a = device("router-a", sightings=(neighbour("router-b"),))
    b = device("router-b", sightings=(neighbour("router-a"),))
    tree = build([a, b])
    assert tree.links == () and tree.cycles == ()


def test_a_station_listing_its_ap_as_a_neighbour_keeps_the_registration_edge():
    ap = device("ap-1", mode="ap-bridge", sightings=(neighbour("cpe-1"),))
    station = device("cpe-1", sightings=(registered("ap-1"), neighbour("ap-1")))
    tree = build([ap, station])
    assert tree.links == (Link(ap.key, station.key, "wlan1", Source.REGISTRATION),)
    assert tree.cycles == () and tree.complete


def test_a_neighbour_seen_from_one_side_only_is_still_an_edge():
    router = device("router")
    behind = device("switch", sightings=(neighbour("router"),))
    assert links_from_sightings([router, behind]) == [
        Link(router.key, behind.key, "ether1", Source.NEIGHBOR)]


def test_a_device_does_not_become_its_own_parent():
    lonely = device("ap-1", sightings=(registered("ap-1"),))
    assert links_from_sightings([lonely]) == []


def test_an_edge_to_something_that_was_never_found_is_dropped():
    """The AP is on somebody else's network; the station is still a device we know."""
    station = device("cpe-1", sightings=(registered("ap-nobody-reached"),))
    tree = build([station])
    assert tree.links == () and tree.orphans == (station.key,)
    assert not tree.complete


# ------------------------------------------------------------------ what is missing


def test_one_root_and_no_orphan_is_a_complete_picture():
    core = device("core-1", mode="ap-bridge")
    station = device("cpe-1", sightings=(registered("core-1"),))
    assert build([core, station]).complete


def test_two_roots_mean_the_picture_has_holes():
    """Two separate trees may be the truth, or may be an AP nobody reached. The
    application cannot tell, so it says so."""
    a = device("ap-1", mode="ap-bridge")
    b = device("ap-2", mode="ap-bridge")
    tree = build([a, device("cpe-1", sightings=(registered("ap-1"),)),
                  b, device("cpe-2", sightings=(registered("ap-2"),))])
    assert len(tree.roots) == 2 and not tree.complete


def test_a_device_with_no_edge_at_all_is_an_orphan():
    core = device("core-1", mode="ap-bridge")
    tree = build([core, device("cpe-1", sightings=(registered("core-1"),)),
                  device("alone")])
    assert len(tree.orphans) == 1 and not tree.complete


def test_a_loop_is_reported_and_not_broken_quietly():
    """A stale record: an AP that used to be a station of the device now hanging off it.
    Which edge is the stale one is not this code's guess to make."""
    a, b = device("a"), device("b")
    tree = build([a, b], [Link(a.key, b.key), Link(b.key, a.key)])
    assert tree.cycles and not tree.complete


# ------------------------------------------------------------------ the order


def test_the_leaves_go_first_and_the_backbone_last():
    core = device("core", mode="ap-bridge")
    relay = device("relay", mode="ap-bridge", sightings=(registered("core"),))
    leaf = device("leaf", sightings=(registered("relay"),))
    order = update_order([core, relay, leaf])
    assert [d.identity for d in order.devices] == ["leaf", "relay", "core"]
    assert not order.needs_confirmation


def test_devices_at_the_same_depth_keep_a_stable_order():
    """Two runs of the same scan have to produce the same batch."""
    core = device("core", mode="ap-bridge")
    first = device("cpe-a", sightings=(registered("core"),))
    second = device("cpe-b", sightings=(registered("core"),))
    forwards = update_order([core, first, second])
    backwards = update_order([second, first, core])
    assert forwards.keys == backwards.keys
    assert [d.identity for d in forwards.devices][-1] == "core"


def test_the_device_this_pc_sits_behind_goes_last_whatever_its_depth():
    """Rebooting it ends the batch wherever it appears in the list."""
    core = device("core", mode="ap-bridge")
    relay = device("relay", mode="ap-bridge", sightings=(registered("core"),))
    leaf = device("leaf", sightings=(registered("relay"),))
    order = update_order([core, relay, leaf], here=leaf.key)
    assert [d.identity for d in order.devices] == ["relay", "core", "leaf"]


def test_a_device_that_was_never_confirmed_is_left_out_and_named():
    """An update is never planned from a guess — but the row does not just vanish."""
    core = device("core", mode="ap-bridge")
    order = update_order([core, unconfirmed("maybe-a-mikrotik")])
    assert [d.identity for d in order.devices] == ["core"]
    assert [d.identity for d in order.skipped] == ["maybe-a-mikrotik"]


def test_an_incomplete_picture_makes_the_order_a_suggestion():
    order = update_order([device("a"), device("b")])  # no edges at all
    assert order.needs_confirmation
    assert len(order.devices) == 2  # still ordered, just not trusted


def test_a_loop_does_not_hang_the_ordering():
    a, b = device("a"), device("b")
    order = update_order([a, b], build([a, b], [Link(a.key, b.key), Link(b.key, a.key)]))
    assert len(order.devices) == 2 and order.needs_confirmation


def test_a_chain_four_deep_comes_out_in_order():
    chain = [device("hop0", mode="ap-bridge")]
    for index in range(1, 4):
        chain.append(device(f"hop{index}", mode="ap-bridge",
                            sightings=(registered(f"hop{index - 1}"),)))
    order = update_order(chain)
    assert [d.identity for d in order.devices] == ["hop3", "hop2", "hop1", "hop0"]


# ------------------------------------------------------------------ showing it


def test_the_tree_reads_as_lines_for_a_confirmation():
    core = device("core", mode="ap-bridge")
    leaf = device("leaf", sightings=(registered("core", "wlan2"),))
    alone = device("alone")
    lines = describe(build([core, leaf, alone]), [core, leaf, alone])
    assert "core (wlan2) -> leaf" in lines
    assert "alone: no link found" in lines


def test_the_order_of_one_device_is_never_in_doubt():
    """Whatever the tree around it looks like, there is nothing to get wrong — and asking
    would train the answer out of anyone updating a single CPE."""
    alone = update_order([device("cpe-1")])  # no links at all
    assert alone.topology.orphans and not alone.needs_confirmation


def test_two_devices_with_no_link_between_them_still_need_confirming():
    pair = update_order([device("a"), device("b")])
    assert pair.needs_confirmation
