"""Candidates, merging and classification — the part of detection that touches no network.

The two failures worth testing for are opposites. Splitting one device into three rows is
untidy; merging two devices into one row is dangerous, because an update would then be
planned from the wrong device's state. So the merge is cautious in exactly one direction,
and these tests pin that down.
"""

from __future__ import annotations

from factories import make_inventory, make_radios
from mqs.discover.candidates import (
    Candidate,
    Confidence,
    Sighting,
    Source,
    confirmed,
    counts,
    from_sighting,
    normalise_mac,
    refused,
)
from mqs.discover.classify import (
    DeviceKind,
    ModelTable,
    classify,
    default_models,
    merged_models,
)
from mqs.discover.merge import (
    address_conflicts,
    by_mac,
    merge_candidates,
    merge_sightings,
)
from mqs.model.inventory import PackageInfo, WirelessRole

MB = 1024 * 1024
MAC_A = "AA:BB:CC:00:00:01"
MAC_B = "AA:BB:CC:00:00:02"


def seen(source: Source, **kwargs) -> Sighting:
    return Sighting(source=source, **kwargs)


def antenna_inventory(identity: str = "sxt-01", *, serial: str = "SN1",
                      model: str = "RBSXTsq5nD", mode: str = "station-bridge",
                      stations: int = 0, macs: tuple[str, ...] = (MAC_A,),
                      board_name: str = "SXTsq 5 nD"):
    inventory = make_inventory(
        "7.15.3", architecture="arm", model=model, board_name=board_name,
        free_hdd_space=256 * MB, factory_firmware="7.10.0",
        radios=make_radios(("wlan1", "wireless", mode),
                           registrations={"wlan1": stations} if stations else None),
        packages=[PackageInfo(name="routeros", version="7.15.3"),
                  PackageInfo(name="wireless", version="7.15.3")])
    board = inventory.routerboard.model_copy(update={"serial_number": serial})
    interfaces = [i.model_copy(update={"mac_address": macs[index]}) if index < len(macs)
                  else i for index, i in enumerate(inventory.interfaces)]
    return inventory.model_copy(update={"identity": identity, "routerboard": board,
                                        "interfaces": interfaces})


# ------------------------------------------------------------------ MAC spellings


def test_every_spelling_of_a_mac_becomes_one():
    """Sources spell them differently, and a device is merged by its MACs — two spellings
    of one address would split it in two."""
    for text in ("aa-bb-cc-00-00-01", "AABBCC000001", "aa:bb:cc:0:0:1",
                 "AA:BB:CC:00:00:01"):
        assert normalise_mac(text) == MAC_A


def test_something_that_is_not_a_mac_is_left_alone():
    assert normalise_mac("not-a-mac") == "NOT-A-MAC"


# ------------------------------------------------------------------ confidence


def test_a_passive_sighting_gets_no_further_than_probable():
    device = from_sighting(seen(Source.MNDP, macs=[MAC_A], identity="sxt-01",
                                version="7.15.3"))
    assert device.confidence is Confidence.PROBABLE
    assert not device.confidence.updatable


def test_an_open_port_proves_nothing():
    """8291 is Winbox by convention and nothing more; anyone may listen on 8729."""
    device = from_sighting(seen(Source.SCAN, address="192.0.2.5"))
    assert device.confidence is Confidence.UNKNOWN


def test_a_mac_prefix_is_a_weak_signal_and_stays_one():
    device = from_sighting(seen(Source.OUI, macs=[MAC_A]))
    assert device.confidence is Confidence.UNKNOWN


def test_a_platform_that_says_another_vendor_is_believed():
    """MNDP is spoken by other vendors' equipment too; a candidate saying it is not a
    MikroTik is more useful as that than as a false positive."""
    device = from_sighting(seen(Source.MNDP, macs=[MAC_A], platform="Ubiquiti"))
    assert device.confidence is Confidence.UNKNOWN


def test_only_a_login_confirms():
    device = from_sighting(seen(Source.MNDP, macs=[MAC_A], address="192.0.2.5"))
    done = confirmed(device, antenna_inventory())
    assert done.confidence is Confidence.CONFIRMED and done.confidence.updatable
    assert done.serial_number == "SN1" and done.identity == "sxt-01"
    assert done.model == "RBSXTsq5nD" and done.architecture == "arm"
    assert Source.LOGIN in done.sources


def test_a_confirmed_device_keeps_what_it_said_itself():
    """A neighbour table's version is one announcement old the moment it is read."""
    device = confirmed(from_sighting(seen(Source.MNDP, macs=[MAC_A])), antenna_inventory())
    stale = device.with_sighting(seen(Source.NEIGHBOR, macs=[MAC_A], version="6.48.6",
                                      identity="old-name"))
    assert stale.version == "7.15.3" and stale.identity == "sxt-01"


def test_a_login_takes_every_interface_mac():
    """What lets a device found by MNDP on its ethernet and in a registration table by its
    radio be recognised as one device."""
    device = confirmed(from_sighting(seen(Source.MNDP, macs=[MAC_A])),
                       antenna_inventory(macs=(MAC_A, MAC_B)))
    assert MAC_A in device.macs and MAC_B in device.macs


def test_a_refused_login_is_recorded_rather_than_forgotten():
    device = refused(from_sighting(seen(Source.MNDP, macs=[MAC_A])), "wrong password")
    assert device.login_error == "wrong password"
    assert device.confidence is Confidence.PROBABLE  # still not updatable


def test_radios_are_unread_until_somebody_logs_in():
    """Not "no radios" — unread. The update planner refuses exactly this state."""
    device = from_sighting(seen(Source.MNDP, macs=[MAC_A], board="SXTsq 5 nD"))
    assert not device.radios.collected and not device.wireless


def test_counts_tally_the_three_states():
    devices = [from_sighting(seen(Source.MNDP, macs=[MAC_A])),
               from_sighting(seen(Source.SCAN, address="192.0.2.9")),
               confirmed(from_sighting(seen(Source.MNDP, macs=[MAC_B])),
                         antenna_inventory())]
    assert counts(devices) == {Confidence.CONFIRMED: 1, Confidence.PROBABLE: 1,
                               Confidence.UNKNOWN: 1}


# ------------------------------------------------------------------ merging


def test_one_device_seen_three_ways_is_one_device():
    devices = merge_sightings([
        seen(Source.MNDP, macs=[MAC_A], identity="sxt-01", found_at="eth0"),
        seen(Source.NEIGHBOR, macs=["aa-bb-cc-00-00-01"], address="192.0.2.5",
             found_at="core-1"),
        seen(Source.REGISTRATION, macs=[MAC_A], found_at="ap-1", via_interface="wlan1"),
    ])
    assert len(devices) == 1
    assert devices[0].sources == {Source.MNDP, Source.NEIGHBOR, Source.REGISTRATION}
    assert devices[0].addresses == ["192.0.2.5"] and devices[0].identity == "sxt-01"


def test_two_devices_on_one_address_are_reported_not_merged():
    """Addresses move, are reused and are handed out again. Merging on one costs an update
    planned from the wrong device's state."""
    devices = merge_sightings([
        seen(Source.NEIGHBOR, macs=[MAC_A], address="192.0.2.5", identity="sxt-01"),
        seen(Source.NEIGHBOR, macs=[MAC_B], address="192.0.2.5", identity="lhg-02"),
    ])
    assert len(devices) == 2
    flagged = address_conflicts(devices)
    assert all(d.conflicts for d in flagged)
    assert "192.0.2.5: also lhg-02" in flagged[0].conflicts
    assert "192.0.2.5: also sxt-01" in flagged[1].conflicts


def test_no_conflict_is_reported_when_there_is_none():
    devices = merge_sightings([seen(Source.MNDP, macs=[MAC_A], address="192.0.2.5"),
                               seen(Source.MNDP, macs=[MAC_B], address="192.0.2.6")])
    assert all(not d.conflicts for d in address_conflicts(devices))


def test_a_serial_number_merges_a_device_that_changed_address():
    """The point of preferring the serial: this is the same box, renumbered."""
    old = confirmed(from_sighting(seen(Source.MNDP, macs=[MAC_A], address="192.0.2.5")),
                    antenna_inventory())
    new = confirmed(from_sighting(seen(Source.MNDP, macs=[MAC_B], address="10.0.0.9")),
                    antenna_inventory(macs=(MAC_B,)))
    merged = merge_candidates([old, new])
    assert len(merged) == 1
    assert merged[0].addresses == ["192.0.2.5", "10.0.0.9"]


def test_the_same_mac_with_a_different_serial_is_a_different_device():
    """A radio card or a whole board was moved, and the old record has not caught up."""
    first = confirmed(from_sighting(seen(Source.MNDP, macs=[MAC_A])),
                      antenna_inventory(serial="SN1"))
    second = confirmed(from_sighting(seen(Source.MNDP, macs=[MAC_A])),
                       antenna_inventory(serial="SN2", identity="sxt-99"))
    assert len(merge_candidates([first, second])) == 2


def test_a_rescan_folds_into_what_is_already_known():
    known = confirmed(from_sighting(seen(Source.MNDP, macs=[MAC_A], address="192.0.2.5")),
                      antenna_inventory())
    fresh = from_sighting(seen(Source.MNDP, macs=[MAC_A], address="192.0.2.5",
                               version="7.16.1"))
    merged = merge_candidates([known, fresh])
    assert len(merged) == 1
    # The stale sighting rides along, but what was read off the device still wins.
    assert merged[0].version == "7.15.3" and len(merged[0].sightings) == 3


def test_the_newer_read_of_a_device_read_twice_is_the_one_kept():
    old = confirmed(from_sighting(seen(Source.MNDP, macs=[MAC_A])),
                    antenna_inventory(identity="before")).model_copy(
                        update={"last_seen": 100.0})
    new = confirmed(from_sighting(seen(Source.MNDP, macs=[MAC_A])),
                    antenna_inventory(identity="after")).model_copy(
                        update={"last_seen": 200.0})
    merged = merge_candidates([old, new])
    assert len(merged) == 1 and merged[0].identity == "after"


def test_a_candidate_with_no_mac_is_not_merged_with_anything():
    """A scan hit on an address and nothing else: it may be any of them, so it is its own
    row until something identifies it."""
    devices = merge_sightings([seen(Source.MNDP, macs=[MAC_A], address="192.0.2.5"),
                               seen(Source.SCAN, address="192.0.2.5")])
    assert len(devices) == 2


def test_a_device_can_be_looked_up_by_any_of_its_macs():
    device = confirmed(from_sighting(seen(Source.MNDP, macs=[MAC_A])),
                       antenna_inventory(macs=(MAC_A, MAC_B)))
    assert by_mac([device], "aa-bb-cc-00-00-02") is device


# ------------------------------------------------------------------ classification


def test_a_radio_makes_a_device_wireless_whatever_it_is_called():
    unknown = confirmed(Candidate(), antenna_inventory(model="RBNEVERHEARDOF",
                                                       board_name="Mystery 5",
                                                       mode="ap-bridge"))
    verdict = classify(unknown)
    assert verdict.wireless and verdict.role is WirelessRole.AP
    assert verdict.category == "" and verdict.kind is DeviceKind.UNKNOWN


def test_a_known_name_adds_a_category_to_a_wireless_row():
    device = confirmed(Candidate(), antenna_inventory(model="RBSXTsq5nD"))
    verdict = classify(device)
    assert verdict.wireless and verdict.category == "SXT Square"
    assert verdict.kind is DeviceKind.PTP and verdict.role is WirelessRole.STATION


def test_a_known_name_with_no_radio_is_not_promoted_on_the_strength_of_it():
    """Either nobody has logged in yet, or the name is misleading. Worth showing, never
    worth acting on."""
    device = from_sighting(seen(Source.MNDP, macs=[MAC_A], board="SXTsq 5 nD"))
    verdict = classify(device)
    assert not verdict.wireless and verdict.name_suggests_wireless
    assert verdict.category == "SXT Square"


def test_the_role_comes_from_the_mode_not_the_product():
    """A dish configured as an AP is an AP."""
    dish = confirmed(Candidate(), antenna_inventory(model="RBSXTsq5nD", mode="ap-bridge",
                                                    stations=4))
    assert classify(dish).role is WirelessRole.AP


def test_an_ap_that_also_has_a_station_radio_counts_as_an_ap():
    """It carries other devices, and that is what decides where it goes in an update."""
    inventory = antenna_inventory(mode="ap-bridge")
    both = inventory.model_copy(update={
        "radios": make_radios(("wlan1", "wireless", "ap-bridge"),
                              ("wlan2", "wireless", "station-bridge"))})
    assert classify(confirmed(Candidate(), both)).role is WirelessRole.AP


def test_sixty_gigahertz_is_flagged_from_the_menu():
    inventory = antenna_inventory(model="RBwAPG-60ad")
    link = inventory.model_copy(update={
        "radios": make_radios(("wlan60-1", "w60g", "bridge"))})
    verdict = classify(confirmed(Candidate(), link))
    assert verdict.sixty_ghz and verdict.kind is DeviceKind.SIXTY
    assert verdict.category == "wAP 60G"


def test_the_model_wins_over_the_board_name():
    """The model is the hardware; the board name is marketing MikroTik reuses."""
    table = ModelTable.from_mapping({
        "models": {"RBSXT": {"label": "from model", "kind": "ptp"}},
        "categories": {"hAP": {"label": "from board", "kind": "ap"}}})
    assert table.lookup("RBSXTsq5nD", "hAP ac lite").label == "from model"
    assert table.lookup("", "hAP ac lite").label == "from board"


def test_the_longest_prefix_wins():
    assert default_models().lookup("RBwAPG-60ad").label == "wAP 60G"
    assert default_models().lookup("RBwAPG-5HacT2HnD").label == "wAP"


def test_a_board_nobody_listed_is_simply_unlisted():
    assert default_models().lookup("RB4011iGS+") is None


def test_a_user_file_adds_boards_without_replacing_the_table(tmp_path):
    override = tmp_path / "wireless_models.yaml"
    override.write_text('models:\n  RBNEW: { label: "New dish", kind: ptp }\n',
                        encoding="utf-8")
    table = merged_models(override)
    assert table.lookup("RBNEW-5acD").label == "New dish"
    assert table.lookup("RBSXTsq5nD").label == "SXT Square"


def test_a_missing_user_file_changes_nothing(tmp_path):
    assert merged_models(tmp_path / "nope.yaml") is default_models()


def test_a_device_with_no_radio_and_no_matching_name_says_nothing():
    plain = confirmed(Candidate(), make_inventory("7.15.3", architecture="arm",
                                                  board_name="RB4011iGS+"))
    verdict = classify(plain)
    assert not verdict.wireless and not verdict.name_suggests_wireless
    assert verdict.summary == "-"
