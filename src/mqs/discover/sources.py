"""Asking the devices already reached about the ones behind them.

MNDP only crosses its own L2 domain, which on a network of antennas is the smallest part
of it: everything past the first radio hop is invisible from a laptop. The devices
themselves are not so limited. A router's `/ip neighbor` sees what is on its own
interfaces, and an AP's registration table names every station on it — which is both a
list of devices and, more usefully, the edges of the tree an update order needs.

Every reader here tolerates a device that does not have the menu, answers in the other
major version's field names, or answers with nothing. None of them raise on a device that
is simply different; a source that falls over takes the whole scan with it, and the point
of having several is that one can fail.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from mqs.discover.candidates import Sighting, Source
from mqs.model.inventory import WirelessMenu
from mqs.model.version import RosVersion
from mqs.transport.base import CommandError, Reply, Transport, TransportError
from mqs.transport.mndp import DiscoveredDevice

# `/ip neighbor print detail`. v6 and v7 agree on most of it; `interface` is plural on
# some versions (`interface=ether1,ether2`) and the identity is sometimes `identity` and
# sometimes `name`. Every spelling that has been seen is read, and a field that is missing
# is simply missing — NUTNO OVĚŘIT which versions use which.
NEIGHBOR_IDENTITY = ("identity", "name")
NEIGHBOR_VERSION = ("version", "software-version")
NEIGHBOR_BOARD = ("board", "board-name", "model")
NEIGHBOR_PLATFORM = ("platform",)
NEIGHBOR_ADDRESS = ("address", "address4", "ipv4-address")
NEIGHBOR_MAC = ("mac-address", "mac")
NEIGHBOR_INTERFACE = ("interface", "local-interface")

REGISTRATION_PATHS: dict[WirelessMenu, str] = {
    WirelessMenu.WIRELESS: "/interface/wireless/registration-table/print",
    WirelessMenu.WIFI: "/interface/wifi/registration-table/print",
}
# A station in a registration table is named by its MAC and little else. v7's `wifi` table
# sometimes carries the far end's identity; the legacy one does not.
REGISTRATION_MAC = ("mac-address", "mac")
REGISTRATION_IDENTITY = ("identity", "name", "comment")
REGISTRATION_INTERFACE = ("interface", "master-interface")


def _first(row: dict[str, str], keys: Sequence[str]) -> str:
    return next((row[key] for key in keys if row.get(key)), "")


def _optional(transport: Transport, path: str, /, **args: object) -> Reply | None:
    """None when the device has no such menu — not an error, just a different device."""
    try:
        return transport.execute(path, **args)
    except CommandError:
        return None
    except TransportError:
        # The session died mid-scan. One source failing is survivable by design, and the
        # caller finds out when the next read of this device fails too.
        return None


def from_mndp(device: DiscoveredDevice) -> Sighting:
    """An MNDP announcement, as a sighting. Pure: the scanner has already done the work."""
    return Sighting(
        source=Source.MNDP,
        found_at=device.local_interface,
        address=str(device.ipv4) if device.ipv4 else device.source_ip,
        macs=[device.mac] if device.mac else [],
        identity=device.identity,
        version=device.version,
        board=device.board,
        platform=device.platform,
        note=interface_note(device),
    )


def interface_note(device: DiscoveredDevice) -> str:
    """Which of the device's own interfaces the announcement left from, when it said."""
    return f"from {device.interface_name}" if device.interface_name else ""


def read_neighbors(transport: Transport, *, found_at: str) -> list[Sighting]:
    """`/ip neighbor print detail` on a device already reached.

    This is what sees past the first radio hop. ``found_at`` names the device doing the
    looking, which is what the topology matches an edge back to.
    """
    rows = _optional(transport, "/ip/neighbor/print", detail=True)
    if rows is None:
        rows = _optional(transport, "/ip/neighbor/print") or []
    sightings: list[Sighting] = []
    for row in rows:
        mac = _first(row, NEIGHBOR_MAC)
        address = _first(row, NEIGHBOR_ADDRESS)
        if not mac and not address:
            continue
        sightings.append(Sighting(
            source=Source.NEIGHBOR,
            found_at=found_at,
            address=address,
            macs=[mac] if mac else [],
            identity=_first(row, NEIGHBOR_IDENTITY),
            version=_first(row, NEIGHBOR_VERSION),
            board=_first(row, NEIGHBOR_BOARD),
            platform=_first(row, NEIGHBOR_PLATFORM),
            via_interface=_first(row, NEIGHBOR_INTERFACE).split(",")[0].strip(),
        ))
    return sightings


def read_registrations(transport: Transport, version: RosVersion, *,
                       found_at: str) -> list[Sighting]:
    """Every station on this device's radios, and the radio each one is on.

    The strongest edge there is: an AP listing a station is the AP saying it carries that
    station, which is exactly what decides the update order. A station has no registration
    table of its own and simply answers with nothing.
    """
    sightings: list[Sighting] = []
    for menu, path in REGISTRATION_PATHS.items():
        if menu is WirelessMenu.WIFI and version.major < 7:
            continue
        for row in _optional(transport, path) or []:
            mac = _first(row, REGISTRATION_MAC)
            if not mac:
                continue
            sightings.append(Sighting(
                source=Source.REGISTRATION,
                found_at=found_at,
                macs=[mac],
                identity=_first(row, REGISTRATION_IDENTITY),
                via_interface=_first(row, REGISTRATION_INTERFACE),
                note=menu.value,
            ))
    sightings.extend(read_sixty_peers(transport, found_at=found_at))
    return sightings


def read_sixty_peers(transport: Transport, *, found_at: str) -> list[Sighting]:
    """The far end of each 60 GHz link.

    There is no registration table for w60g — one peer per interface, named in
    `remote-address` — so the link is read off the interface itself.
    """
    sightings = []
    for row in _optional(transport, "/interface/w60g/print") or []:
        peer = row.get("remote-address", "")
        if not peer:
            continue
        sightings.append(Sighting(
            source=Source.REGISTRATION, found_at=found_at, macs=[peer],
            via_interface=row.get("name", ""), note=WirelessMenu.W60G.value))
    return sightings


def read_device(transport: Transport, version: RosVersion, *,
                found_at: str) -> list[Sighting]:
    """Everything one reached device can say about the others: neighbours and stations."""
    return [*read_neighbors(transport, found_at=found_at),
            *read_registrations(transport, version, found_at=found_at)]


def crawl(
    seeds: Sequence[tuple[str, Transport, RosVersion]],
    on_source: Callable[[str, int], None] = lambda where, found: None,
) -> list[Sighting]:
    """Ask each reached device about its neighbours, reporting as it goes.

    One device failing is not the crawl failing: its readers return nothing and the next
    device is asked. A slow one costs its own timeout and no one else's, which is the
    transport's business rather than this function's.
    """
    sightings: list[Sighting] = []
    for name, transport, version in seeds:
        found = read_device(transport, version, found_at=name)
        sightings.extend(found)
        on_source(name, len(found))
    return sightings
