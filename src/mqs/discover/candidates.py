"""What the application thinks it has found, and how sure it is.

No single way of finding a MikroTik is reliable. MNDP only reaches its own L2 domain and
only where discovery is left on; a neighbour table only shows what its router can see;
SNMP needs a community; an open port proves nothing at all. So every source contributes
*candidates*, the candidates are merged, and each merged device carries the reason it is
believed to be what it is.

Only `CONFIRMED` — logged in, data read off the device itself — is ever allowed to be
updated. Everything else is for the engineer to look at, and the reason it got no further
is kept with it rather than thrown away.

Everything in this module is pure: no sockets, no device, no clock beyond what is handed
in. The sources that do the reaching live beside it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from mqs.model.inventory import DeviceInventory, WirelessSnapshot
from mqs.model.version import RosVersion


class Confidence(StrEnum):
    """How far the application got with a device.

    The order matters: `max()` over a merged device's sources is its confidence.
    """

    UNKNOWN = "unknown"  # it answers, and nothing says MikroTik
    PROBABLE = "probable"  # passive sources agree, nobody logged in
    CONFIRMED = "confirmed"  # logged in, and the data came off the device

    @property
    def rank(self) -> int:
        return {Confidence.UNKNOWN: 0, Confidence.PROBABLE: 1, Confidence.CONFIRMED: 2}[self]

    @property
    def updatable(self) -> bool:
        """An update is planned for this device, or it is not. There is no middle."""
        return self is Confidence.CONFIRMED


class Source(StrEnum):
    """Where a candidate came from. Several per device is the normal case."""

    MNDP = "mndp"  # MikroTik Neighbor Discovery, UDP 5678
    NEIGHBOR = "neighbor"  # /ip neighbor on a router already reached
    REGISTRATION = "registration"  # an AP's or a link's registration table
    SNMP = "snmp"  # sysObjectID under the MikroTik enterprise OID
    SCAN = "scan"  # a port answered
    LIBRENMS = "librenms"  # imported, read-only
    OUI = "oui"  # the MAC prefix is registered to MikroTik
    LOGIN = "login"  # logged in and read the device

    @property
    def confirms(self) -> bool:
        return self is Source.LOGIN

    @property
    def proves_mikrotik(self) -> bool:
        """Whether this source, on its own, says the device is a MikroTik.

        An open port does not: 8291 is Winbox by convention and nothing more, and anyone
        may listen on 8729. A MAC prefix is a weak signal for the same reason — it can be
        spoofed, and a reused NIC outlives its board.
        """
        return self in (Source.MNDP, Source.NEIGHBOR, Source.REGISTRATION, Source.SNMP,
                        Source.LOGIN)


def normalise_mac(text: str) -> str:
    """`aa-bb-cc-00-11-22`, `AABBCC001122`, `aa:bb:cc:0:11:22` -> `AA:BB:CC:00:11:22`.

    Sources spell MAC addresses differently — MNDP builds its own, RouterOS prints colons,
    SNMP hands back bytes — and a device is merged by the set of its MAC addresses, so two
    spellings of one address would split it in two.
    """
    digits = "".join(c for c in text if c.isalnum()).upper()
    if len(digits) != 12:
        parts = [p for p in text.replace("-", ":").split(":") if p]
        if len(parts) != 6:
            return text.strip().upper()
        digits = "".join(p.rjust(2, "0") for p in parts).upper()
    return ":".join(digits[i:i + 2] for i in range(0, 12, 2))


NO_MAC = "00:00:00:00:00:00"


def device_macs(macs: Iterable[str]) -> list[str]:
    """``macs`` normalised, without the ones that name no device.

    v7's `lo` reports 00:00:00:00:00:00, so every v7 box read by a login has it — and a
    MAC every device shares would make any two of them one device to a match by MAC.
    """
    return sorted({mac for mac in (normalise_mac(m) for m in macs if m.strip())
                   if mac != NO_MAC})


class Sighting(BaseModel):
    """One source's word on one device, kept as the source gave it.

    Nothing is reconciled here. A sighting that disagrees with another is a fact about the
    network worth showing, and merging is where that gets decided.
    """

    model_config = ConfigDict(extra="forbid")

    source: Source
    # Where it was seen: the PC's NIC for MNDP, the router's identity for a neighbour or
    # registration table, the subnet for a scan.
    found_at: str = ""
    address: str = ""
    macs: list[str] = Field(default_factory=list)
    identity: str = ""
    version: str = ""
    board: str = ""
    platform: str = ""
    # The interface on the *other* device that saw this one: an AP's wlan1 for a station
    # in its registration table. This is what the topology is built from.
    via_interface: str = ""
    note: str = ""
    # Seen on the interface this PC's own session came in by: beside the reporting device
    # or upstream of it, never behind it — so a sighting, but not an edge of the tree.
    upstream: bool = False

    def normalised(self) -> Sighting:
        return self.model_copy(update={"macs": device_macs(self.macs)})

    @property
    def anonymous(self) -> bool:
        """A MAC and nothing else: no name, no address, nothing a device announces.

        What an AP's registration table says about a phone, or a neighbour table about a
        PC. It can still be one more sighting of a device already known by that MAC, but
        on its own it is not a device the list could log in to or update.
        """
        return not (self.identity or self.address or self.version or self.board
                    or self.platform)

    @property
    def mikrotik(self) -> bool:
        """Whether this sighting, by itself, says MikroTik.

        A `platform` naming something else is taken at its word: MNDP is spoken by other
        vendors' equipment too (Ubiquiti answered it for years), and a candidate that says
        it is not a MikroTik is more useful as that than as a false positive.
        """
        if self.platform and "mikrotik" not in self.platform.lower():
            return False
        return self.source.proves_mikrotik


class Candidate(BaseModel):
    """One device, as every source that saw it describes it together.

    `inventory` is filled once somebody logs in, and is what makes the device
    `CONFIRMED` — and the only thing an update is ever planned from.
    """

    model_config = ConfigDict(extra="forbid")

    macs: list[str] = Field(default_factory=list)
    addresses: list[str] = Field(default_factory=list)
    identity: str = ""
    serial_number: str = ""
    board: str = ""
    model: str = ""
    version: str = ""
    architecture: str = ""
    sightings: list[Sighting] = Field(default_factory=list)
    inventory: DeviceInventory | None = None
    # Why a login did not happen or did not work. Kept so the table can say "wrong
    # password" rather than leaving a device looking merely undiscovered.
    login_error: str = ""
    # Conflicting claims found while merging — the same address answering as two devices,
    # most often. Never resolved quietly.
    conflicts: list[str] = Field(default_factory=list)
    last_seen: float = 0.0

    @property
    def sources(self) -> set[Source]:
        return {s.source for s in self.sightings}

    @property
    def confidence(self) -> Confidence:
        if self.inventory is not None:
            return Confidence.CONFIRMED
        if any(s.mikrotik for s in self.sightings):
            return Confidence.PROBABLE
        return Confidence.UNKNOWN

    @property
    def radios(self) -> WirelessSnapshot:
        """What it has on the air. Empty and uncollected until somebody logs in — which
        is exactly the state the update planner refuses to work from."""
        return self.inventory.radios if self.inventory else WirelessSnapshot()

    @property
    def wireless(self) -> bool:
        """A wireless device, by the only criterion that holds: it has radios.

        Before a login this is False whatever the model name suggests, because nothing has
        looked. `classify.py` says what the name *suggests*, separately and as such.
        """
        return self.radios.has_radios

    @property
    def ros_version(self) -> RosVersion | None:
        """The version as something comparable.

        `find` rather than `parse`: a version read off the device is clean, but one from
        an announcement comes with the build time after it, and a strict parse there
        yields nothing at all — which would mean two scans never noticing a device that
        moved version.
        """
        if self.inventory is not None:
            return self.inventory.version
        return RosVersion.find(self.version) if self.version else None

    @property
    def address(self) -> str:
        return self.addresses[0] if self.addresses else ""

    @property
    def key(self) -> str:
        """A stable identity for the inventory file, best available first."""
        if self.serial_number:
            return f"serial:{self.serial_number}"
        if self.macs:
            return f"mac:{self.macs[0]}"
        return f"address:{self.address}"

    def with_sighting(self, sighting: Sighting) -> Candidate:
        return merge_into(self, sighting.normalised())


def merge_into(candidate: Candidate, sighting: Sighting) -> Candidate:
    """One more sighting folded into what is already known.

    What the device itself said when somebody logged in is never overwritten by what a
    passive source thinks: a neighbour table's idea of a version is one announcement old
    the moment it is read, and `/system resource` is not.
    """
    fixed = candidate.inventory is not None
    macs = sorted({*candidate.macs, *sighting.macs})
    addresses = list(candidate.addresses)
    if sighting.address and sighting.address not in addresses:
        addresses.append(sighting.address)
    return candidate.model_copy(update={
        "macs": macs,
        "addresses": addresses,
        "identity": candidate.identity if fixed else (candidate.identity
                                                      or sighting.identity),
        "version": candidate.version if fixed else (candidate.version or sighting.version),
        "board": candidate.board or sighting.board,
        "sightings": [*candidate.sightings, sighting],
    })


def from_sighting(sighting: Sighting) -> Candidate:
    seen = sighting.normalised()
    return Candidate(macs=list(seen.macs),
                     addresses=[seen.address] if seen.address else [],
                     identity=seen.identity, version=seen.version, board=seen.board,
                     sightings=[seen])


def confirmed(candidate: Candidate, inventory: DeviceInventory, *,
              found_at: str = "") -> Candidate:
    """The device after a successful login: everything below is now second-hand.

    The MAC addresses of every interface are taken, not just the one it was found by.
    That is what lets a device found twice — once by MNDP on its management port, once in
    an AP's registration table by its radio — be recognised as one device.
    """
    board = inventory.routerboard
    macs = device_macs([*candidate.macs,
                        *(i.mac_address for i in inventory.interfaces if i.mac_address)])
    return candidate.model_copy(update={
        "macs": macs,
        "identity": inventory.identity or candidate.identity,
        "serial_number": board.serial_number if board else "",
        "model": board.model if board else "",
        "board": inventory.resource.board_name or candidate.board,
        "version": str(inventory.version),
        "architecture": inventory.resource.architecture,
        "inventory": inventory,
        "login_error": "",
        "sightings": [*candidate.sightings,
                      Sighting(source=Source.LOGIN, found_at=found_at,
                               address=candidate.address,
                               identity=inventory.identity,
                               version=str(inventory.version),
                               board=inventory.resource.board_name)],
    })


def refused(candidate: Candidate, error: str) -> Candidate:
    """A login that did not work. Recorded, and not tried again with the same credentials:
    a device that locks an account out after three goes is worse off for the retry."""
    return candidate.model_copy(update={"login_error": error})


def counts(candidates: Iterable[Candidate]) -> Mapping[Confidence, int]:
    tally = dict.fromkeys(Confidence, 0)
    for candidate in candidates:
        tally[candidate.confidence] += 1
    return tally
