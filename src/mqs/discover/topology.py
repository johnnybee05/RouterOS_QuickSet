"""Which device hangs off which, and therefore what order an update has to go in.

The rule a batch update lives or dies by: **never update a device before the ones behind
it.** Reboot an AP first and every station on it goes away for the two minutes it takes to
come back — and if one of those stations then fails, it fails out of reach. So the order
runs from the leaves inward, and the device the application itself is sitting behind goes
last of all.

The tree is built from what the devices themselves report: an AP's registration table
names the stations on it, a 60 GHz interface names its one peer, and `/ip neighbor` names
what a router can see. None of those is complete — a station whose AP was never reached
has no edge at all — so an incomplete tree says so and the order it produces is offered
for confirmation rather than acted on.

Pure: candidates and edges in, an order out.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from mqs.discover.candidates import Candidate, Source, normalise_mac


@dataclass(frozen=True)
class Link:
    """One device reached through another. ``parent`` carries ``child``.

    ``interface`` is the parent's own interface, which is what makes a link worth showing:
    "ap-1 wlan1" says more than "ap-1" when an AP has three radios.
    """

    parent: str  # Candidate.key
    child: str
    interface: str = ""
    source: Source = Source.REGISTRATION


@dataclass(frozen=True)
class Topology:
    """The tree, and an honest account of what is missing from it."""

    links: tuple[Link, ...] = ()
    roots: tuple[str, ...] = ()  # devices with no parent found
    orphans: tuple[str, ...] = ()  # devices with no edge at all, in either direction
    cycles: tuple[tuple[str, ...], ...] = ()

    @property
    def complete(self) -> bool:
        """Whether the order below can be trusted without someone looking at it.

        One root is a tree. More than one root, any orphan or any cycle means the picture
        has holes, and a batch order drawn from it is a suggestion.
        """
        return not self.orphans and not self.cycles and len(self.roots) <= 1

    def children_of(self, key: str) -> list[str]:
        return [link.child for link in self.links if link.parent == key]

    def parents_of(self, key: str) -> list[str]:
        return [link.parent for link in self.links if link.child == key]

    def depth_of(self, key: str) -> int:
        """Hops from the nearest root. Used for ordering, so a cycle stops rather than
        spins: a key already on the path is as deep as it is going to get."""
        return self._depth(key, set())

    def _depth(self, key: str, seen: set[str]) -> int:
        if key in seen:
            return len(seen)
        parents = self.parents_of(key)
        if not parents:
            return 0
        seen = {*seen, key}
        return 1 + max(self._depth(parent, seen) for parent in parents)


def links_from_sightings(devices: Sequence[Candidate]) -> list[Link]:
    """Edges drawn from how each device was found.

    A registration-table sighting is the strong one: an AP listing a station is the AP
    saying it carries that station. `found_at` holds the device that did the reporting,
    which is matched back to a candidate by identity, address or serial.
    """
    by_name = _index(devices)
    links: list[Link] = []
    for device in devices:
        for sighting in device.sightings:
            if sighting.source not in (Source.REGISTRATION, Source.NEIGHBOR):
                continue
            parent = by_name.get(_normalise_name(sighting.found_at))
            if parent is None or parent == device.key:
                continue
            link = Link(parent, device.key, sighting.via_interface, sighting.source)
            if link not in links:
                links.append(link)
    return links


def _index(devices: Iterable[Candidate]) -> dict[str, str]:
    """Every name a device might be reported under, mapped to its key."""
    index: dict[str, str] = {}
    for device in devices:
        for name in (device.identity, device.serial_number, *device.addresses,
                     *device.macs):
            if name:
                index.setdefault(_normalise_name(name), device.key)
    return index


def _normalise_name(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    # A MAC may be reported in any spelling; anything else is matched case-insensitively.
    if sum(c.isalnum() for c in stripped) == 12 and not stripped.replace(":", "").replace(
            "-", "").isalpha():
        return normalise_mac(stripped).lower()
    return stripped.lower()


def build(devices: Sequence[Candidate], links: Sequence[Link] | None = None) -> Topology:
    """The tree over ``devices``, with whatever is missing from it named."""
    found = list(links) if links is not None else links_from_sightings(devices)
    keys = {d.key for d in devices}
    found = [link for link in found if link.parent in keys and link.child in keys]
    with_parent = {link.child for link in found}
    with_edge = with_parent | {link.parent for link in found}
    roots = sorted(k for k in keys if k not in with_parent and k in with_edge)
    orphans = sorted(k for k in keys if k not in with_edge)
    return Topology(tuple(found), tuple(roots), tuple(orphans),
                    tuple(_cycles(found, keys)))


def _cycles(links: Sequence[Link], keys: set[str]) -> list[tuple[str, ...]]:
    """Every set of devices that reach each other in a loop.

    Not a thing a radio link should produce, and exactly the thing a stale record does —
    an AP that used to be a station of the device now hanging off it. Reported, never
    broken silently, because which edge is the stale one is not this code's guess to make.
    """
    children: dict[str, list[str]] = {}
    for link in links:
        children.setdefault(link.parent, []).append(link.child)
    found: list[tuple[str, ...]] = []
    seen: set[str] = set()

    def walk(key: str, path: list[str]) -> None:
        if key in path:
            loop = tuple(sorted(path[path.index(key):]))
            if loop not in found:
                found.append(loop)
            return
        if key in seen:
            return
        seen.add(key)
        for child in children.get(key, []):
            walk(child, [*path, key])

    for key in sorted(keys):
        walk(key, [])
    return found


# ------------------------------------------------------------------ the order


@dataclass(frozen=True)
class UpdateOrder:
    """Devices in the order they may be updated, and why that order is or is not safe."""

    devices: tuple[Candidate, ...] = ()
    topology: Topology = field(default_factory=Topology)
    # Devices left out because they are not CONFIRMED: an update is never planned from a
    # guess, so they are named rather than silently dropped.
    skipped: tuple[Candidate, ...] = ()

    @property
    def needs_confirmation(self) -> bool:
        """An incomplete picture means the order is a suggestion for someone to approve.

        Except where there is nothing to get wrong: the order of one device is the device,
        whatever the tree around it looks like. Asking there would train the answer out of
        anyone updating a single CPE.
        """
        return len(self.devices) > 1 and not self.topology.complete

    @property
    def keys(self) -> list[str]:
        return [d.key for d in self.devices]


def update_order(devices: Sequence[Candidate], topology: Topology | None = None, *,
                 here: str = "") -> UpdateOrder:
    """Leaves first, the backbone last.

    Ordered by depth, deepest first, so nothing is rebooted while something behind it
    still has to be reached through it. Within one depth the order is by key, which is
    arbitrary but stable — two runs of the same scan produce the same batch.

    ``here`` is the device this PC sits behind, if it is known; it is forced to the very
    end regardless of depth, because rebooting it ends the batch wherever it appears.
    """
    tree = topology if topology is not None else build(devices)
    allowed = [d for d in devices if d.confidence.updatable]
    skipped = [d for d in devices if not d.confidence.updatable]
    ordered = sorted(allowed, key=lambda d: (-tree.depth_of(d.key), d.key))
    if here:
        ordered = [d for d in ordered if d.key != here] + \
                  [d for d in ordered if d.key == here]
    return UpdateOrder(tuple(ordered), tree, tuple(skipped))


def describe(topology: Topology, devices: Sequence[Candidate]) -> list[str]:
    """The tree as lines for a log or a confirmation dialog, deepest last."""
    names = {d.key: (d.identity or d.key) for d in devices}
    lines: list[str] = []
    for link in sorted(topology.links, key=lambda link: (link.parent, link.child)):
        through = f" ({link.interface})" if link.interface else ""
        lines.append(f"{names.get(link.parent, link.parent)}{through}"
                     f" -> {names.get(link.child, link.child)}")
    for orphan in topology.orphans:
        lines.append(f"{names.get(orphan, orphan)}: no link found")
    return lines
