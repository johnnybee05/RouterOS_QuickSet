"""The inventory file, and what a second scan says the first one did not.

A scan on its own is a list to look up. A scan next to the one before it is the thing
that gets noticed — and "this antenna had a radio last week" is the thing that has to be.
"""

from __future__ import annotations

import json

import pytest

from factories import make_inventory, make_radios
from mqs.discover.candidates import Candidate, Sighting, Source, confirmed, from_sighting
from mqs.discover.store import (
    CSV_COLUMNS,
    ChangeKind,
    Inventory,
    InventoryError,
    compare,
    load,
    save,
    to_csv,
    to_rows,
    updatable,
)
from mqs.model.inventory import PackageInfo, RouterboardInfo, WirelessSnapshot

MB = 1024 * 1024
MAC = "AA:BB:CC:00:00:01"


def antenna(identity: str = "sxt-01", *, version: str = "7.15.3", serial: str = "SN1",
            radios=None, address: str = "192.0.2.5", mac: str = MAC) -> Candidate:
    inventory = make_inventory(
        version, architecture="arm", model="RBSXTsq5nD", board_name="SXTsq 5 nD",
        free_hdd_space=256 * MB, factory_firmware="7.10.0",
        radios=radios if radios is not None else make_radios(
            ("wlan1", "wireless", "station-bridge")),
        packages=[PackageInfo(name="routeros", version=version),
                  PackageInfo(name="wireless", version=version)])
    board = RouterboardInfo(routerboard=True, model="RBSXTsq5nD", serial_number=serial,
                            current_firmware="7.10.0", upgrade_firmware=version)
    inventory = inventory.model_copy(update={"identity": identity, "routerboard": board})
    base = Candidate(addresses=[address], macs=[mac],
                     sightings=[Sighting(source=Source.MNDP, macs=[mac],
                                         address=address, found_at="eth0")])
    return confirmed(base, inventory)


def guess(identity: str = "maybe", mac: str = "AA:BB:CC:00:00:99") -> Candidate:
    return from_sighting(Sighting(source=Source.MNDP, macs=[mac], identity=identity,
                                  found_at="eth0"))


# ------------------------------------------------------------------ the file


def test_a_missing_file_is_an_empty_inventory_not_an_error(tmp_path):
    """The right answer for a first run."""
    assert load(tmp_path / "inventory.json").devices == []


def test_an_inventory_survives_a_round_trip(tmp_path):
    path = tmp_path / "inventory.json"
    save(path, Inventory(saved_at=1234.0, devices=[antenna(), guess()]))
    back = load(path)
    assert [d.identity for d in back.devices] == ["sxt-01", "maybe"]
    assert back.devices[0].inventory is not None
    assert back.devices[0].radios.names == ["wlan1"]
    assert back.devices[0].serial_number == "SN1"
    assert back.saved_at == 1234.0


def test_the_file_is_readable_json(tmp_path):
    path = tmp_path / "inventory.json"
    save(path, Inventory(devices=[antenna()]))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["format"] == 1 and data["devices"][0]["identity"] == "sxt-01"


def test_an_interrupted_save_leaves_the_old_file_alone(tmp_path):
    """Written beside the target and moved in, so there is never half an inventory where
    the whole one was."""
    path = tmp_path / "inventory.json"
    save(path, Inventory(devices=[antenna("first")]))
    save(path, Inventory(devices=[antenna("second")]))
    assert load(path).devices[0].identity == "second"
    assert not list(tmp_path.glob("*.part"))


def test_a_file_from_another_format_is_refused_rather_than_guessed_at(tmp_path):
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps({"format": 99, "devices": []}), encoding="utf-8")
    with pytest.raises(InventoryError, match="format 1"):
        load(path)


def test_a_file_that_is_not_json_is_refused(tmp_path):
    path = tmp_path / "inventory.json"
    path.write_text("not json at all", encoding="utf-8")
    with pytest.raises(InventoryError):
        load(path)


# ------------------------------------------------------------------ what changed


def test_a_device_that_lost_its_radio_is_the_loudest_change():
    """What the whole application is built around noticing."""
    before = [antenna()]
    after = [antenna(radios=WirelessSnapshot(collected=True))]
    diff = compare(before, after)
    assert diff.serious and diff.serious[0].kind is ChangeKind.RADIO_LOST
    assert "wlan1" in diff.serious[0].detail


def test_a_version_that_went_backwards_is_serious_too():
    """Something installed itself that nobody planned."""
    diff = compare([antenna(version="7.16.1")], [antenna(version="7.15.3")])
    downgrade = diff.of_kind(ChangeKind.DOWNGRADE)
    assert downgrade and downgrade[0].detail == "7.16.1 -> 7.15.3"
    assert diff.serious


def test_a_version_that_went_forwards_is_worth_knowing_and_not_serious():
    diff = compare([antenna(version="7.15.3")], [antenna(version="7.16.1")])
    assert diff.of_kind(ChangeKind.VERSION) and not diff.serious


def test_a_new_wireless_device_is_marked_as_such():
    diff = compare([], [antenna()])
    assert diff.of_kind(ChangeKind.RADIO_NEW)
    assert not diff.of_kind(ChangeKind.NEW)


def test_a_new_device_that_was_never_logged_into_is_merely_new():
    diff = compare([], [guess()])
    assert diff.of_kind(ChangeKind.NEW) and not diff.of_kind(ChangeKind.RADIO_NEW)


def test_a_device_that_was_not_seen_is_reported_and_not_deleted():
    """MNDP is missed as often as it is heard; a record that vanishes because a laptop
    was on the wrong port is worse than a stale one."""
    diff = compare([antenna()], [])
    gone = diff.of_kind(ChangeKind.GONE)
    assert gone and gone[0].name == "sxt-01"
    assert [d.identity for d in diff.merged] == ["sxt-01"]


def test_a_renamed_device_is_noticed_by_its_serial():
    diff = compare([antenna("old-name")], [antenna("new-name")])
    renamed = diff.of_kind(ChangeKind.IDENTITY)
    assert renamed and renamed[0].detail == "old-name -> new-name"
    assert not diff.of_kind(ChangeKind.GONE)  # the same box, not two


def test_a_new_address_is_noticed():
    diff = compare([antenna(address="192.0.2.5")], [antenna(address="10.0.0.9")])
    moved = diff.of_kind(ChangeKind.ADDRESS)
    assert moved and moved[0].detail == "10.0.0.9"


def test_becoming_loggable_is_a_change_worth_seeing():
    """It was a guess last week and it is a confirmed device now — which means it can be
    updated now, and could not before."""
    before = [from_sighting(Sighting(source=Source.MNDP, macs=[MAC], identity="sxt-01"))]
    diff = compare(before, [antenna()])
    changed = diff.of_kind(ChangeKind.CONFIDENCE)
    assert changed and changed[0].detail == "probable -> confirmed"


def test_an_unchanged_device_says_nothing():
    assert compare([antenna()], [antenna()]).changes == ()


def test_the_serious_changes_come_first():
    diff = compare([antenna("a"), antenna("b", serial="SN2", mac="AA:BB:CC:00:00:02")],
                   [antenna("a", version="7.16.1"),
                    antenna("b", serial="SN2", mac="AA:BB:CC:00:00:02",
                            radios=WirelessSnapshot(collected=True))])
    assert diff.changes[0].kind is ChangeKind.RADIO_LOST


def test_a_radio_is_not_called_lost_when_nobody_looked():
    """An unread snapshot is not an empty one, and reporting it as a lost radio every
    time a passive scan runs would make the whole diff useless."""
    diff = compare([antenna()], [guess("sxt-01", mac=MAC)])
    assert not diff.of_kind(ChangeKind.RADIO_LOST)


# ------------------------------------------------------------------ the table


def test_every_column_of_the_table_is_filled_from_the_device():
    rows = to_rows([antenna()])
    assert len(rows) == 1
    row = rows[0]
    assert row["identity"] == "sxt-01" and row["address"] == "192.0.2.5"
    assert row["model"] == "RBSXTsq5nD" and row["category"] == "SXT Square"
    assert row["role"] == "station" and row["version"] == "7.15.3"
    assert row["architecture"] == "arm" and row["wireless_stack"] == "wireless"
    assert "wireless 7.15.3" in row["packages"]
    assert row["firmware"] == "7.10.0 -> 7.15.3"
    assert row["confidence"] == "confirmed"
    assert "mndp" in row["sources"] and "login" in row["sources"]
    assert row["found_at"] == "eth0"


def test_a_device_nobody_logged_into_fills_what_it_can():
    row = to_rows([guess()])[0]
    assert row["identity"] == "maybe" and row["confidence"] == "probable"
    assert row["role"] == "" and row["packages"] == ""


def test_the_csv_is_semicolon_separated_for_czech_excel():
    """A comma-separated file lands in one column where a comma is a decimal point."""
    text = to_csv(to_rows([antenna()]))
    header, first = text.splitlines()[:2]
    assert header == ";".join(CSV_COLUMNS)
    assert first.split(";")[0] == "sxt-01"


def test_a_value_with_a_semicolon_is_quoted():
    device = antenna()
    text = to_csv(to_rows([device.model_copy(update={"identity": 'a;b"c'})]))
    assert '"a;b""c"' in text


def test_only_confirmed_devices_are_offered_for_an_update():
    assert [d.identity for d in updatable([antenna(), guess()])] == ["sxt-01"]
