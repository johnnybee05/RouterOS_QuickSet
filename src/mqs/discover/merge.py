"""Putting the sightings of one device together, and refusing to put two devices together.

A device has as many MAC addresses as it has interfaces and as many addresses as someone
gave it, so the same box arrives from MNDP, from a router's neighbour table and from an
AP's registration table looking like three. Merging them is what makes an inventory an
inventory.

The merge is deliberately cautious in one direction only. Two sightings are the same
device when something identifies them as one: a serial number, read off the device, or a
shared MAC address. An IP address is **not** that something — addresses move, are reused,
and are handed out again — so two devices answering on one address are reported as a
conflict and left apart. Splitting one device in two costs a duplicate row; merging two
devices into one costs an update planned from the wrong device's state.

Pure: candidates in, candidates out.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from mqs.discover.candidates import (
    Candidate,
    Sighting,
    from_sighting,
    merge_into,
    normalise_mac,
)


def merge_sightings(sightings: Iterable[Sighting]) -> list[Candidate]:
    """Every sighting folded into as few devices as the evidence allows."""
    devices: list[Candidate] = []
    for sighting in sightings:
        seen = sighting.normalised()
        match = _match(devices, seen.macs, "")
        if match is None:
            devices.append(from_sighting(seen))
        else:
            devices[match] = merge_into(devices[match], seen)
    return devices


def merge_candidates(candidates: Sequence[Candidate]) -> list[Candidate]:
    """Merge whole candidates — a fresh scan folded into what the inventory already holds.

    Serial numbers are tried first and alone: two candidates that name different serials
    are different devices even if they share a MAC, which is what happens when a radio
    card or a whole board is moved and the old record has not caught up.
    """
    devices: list[Candidate] = []
    for candidate in candidates:
        match = _match(devices, candidate.macs, candidate.serial_number)
        if match is None:
            devices.append(candidate)
            continue
        devices[match] = _combine(devices[match], candidate)
    return devices


def find_match(devices: Sequence[Candidate], candidate: Candidate) -> Candidate | None:
    """The device in ``devices`` that is the same box as ``candidate``, by the same rules
    the merge uses: serial number, else a shared MAC, never an address.

    Needed outside the merge because a device's key changes the first time somebody logs
    in — it is keyed by MAC until it can be keyed by serial — and a comparison that keyed
    on the key alone would report every first login as one device gone and another
    arrived.
    """
    index = _match(devices, candidate.macs, candidate.serial_number)
    return devices[index] if index is not None else None


def _match(devices: Sequence[Candidate], macs: Sequence[str], serial: str) -> int | None:
    """The device these MAC addresses belong to, or None for a new one."""
    if serial:
        for index, device in enumerate(devices):
            if device.serial_number == serial:
                return index
        # A serial that matches nothing may still be a device already known by its MACs
        # and not yet logged in to; falling through to the MAC test is what picks that up.
    wanted = set(macs)
    if not wanted:
        return None
    for index, device in enumerate(devices):
        if not wanted & set(device.macs):
            continue
        if serial and device.serial_number and device.serial_number != serial:
            continue  # same MAC, different serial: not the same device
        return index
    return None


def _combine(into: Candidate, other: Candidate) -> Candidate:
    """Two records of one device. What was read off the device wins over what was guessed.

    Both inventories being present means the device was read twice; the newer read is the
    one kept, because that is the state an update would be planned from.
    """
    keep_other = other.inventory is not None and (
        into.inventory is None or other.last_seen >= into.last_seen)
    primary, secondary = (other, into) if keep_other else (into, other)
    return primary.model_copy(update={
        "macs": sorted({*into.macs, *other.macs}),
        "addresses": _addresses(into, other),
        "identity": primary.identity or secondary.identity,
        "serial_number": primary.serial_number or secondary.serial_number,
        "board": primary.board or secondary.board,
        "model": primary.model or secondary.model,
        "version": primary.version or secondary.version,
        "architecture": primary.architecture or secondary.architecture,
        "sightings": [*into.sightings, *other.sightings],
        "inventory": primary.inventory or secondary.inventory,
        "login_error": primary.login_error or secondary.login_error,
        "conflicts": sorted({*into.conflicts, *other.conflicts}),
        "last_seen": max(into.last_seen, other.last_seen),
    })


def _addresses(into: Candidate, other: Candidate) -> list[str]:
    merged = list(into.addresses)
    for address in other.addresses:
        if address not in merged:
            merged.append(address)
    return merged


def address_conflicts(devices: Sequence[Candidate]) -> list[Candidate]:
    """Devices sharing an address, each told which other device it shares it with.

    Not an error and not a merge: on a network being surveyed it usually means a stale
    record, a reused address or a device that has already been renumbered, and all three
    are things the engineer wants to see rather than have decided for them.
    """
    owners: dict[str, list[int]] = {}
    for index, device in enumerate(devices):
        for address in device.addresses:
            owners.setdefault(address, []).append(index)
    notes: dict[int, set[str]] = {}
    for address, indexes in owners.items():
        if len(indexes) < 2:
            continue
        for index in indexes:
            others = [_name(devices[other]) for other in indexes if other != index]
            notes.setdefault(index, set()).add(
                f"{address}: also {', '.join(sorted(others))}")
    return [device.model_copy(update={"conflicts": sorted({*device.conflicts,
                                                           *notes[index]})})
            if index in notes else device
            for index, device in enumerate(devices)]


def _name(device: Candidate) -> str:
    return device.identity or device.serial_number or (device.macs[0] if device.macs
                                                       else "?")


def by_address(devices: Sequence[Candidate], address: str) -> Candidate | None:
    return next((d for d in devices if address in d.addresses), None)


def by_mac(devices: Sequence[Candidate], mac: str) -> Candidate | None:
    wanted = normalise_mac(mac)
    return next((d for d in devices if wanted in d.macs), None)
