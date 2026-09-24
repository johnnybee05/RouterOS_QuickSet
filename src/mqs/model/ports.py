"""Port names: the device may call a port something else than its factory name.

RouterOS keeps the factory name of an ethernet port as `default-name` and lets it be
renamed; /export writes a renamed port as
`/interface ethernet set [ find default-name=ether1 ] name=ether1-uplink` (7.20.7).
``DeviceProfile.interface_names`` holds the names the profile gives, keyed by
default-name, and everything else in the profile (bridge members, the WAN port) refers to
a port by the name it has once the script has run.

An import keeps the device's own names, and a reset very likely brings the factory ones
back: right after one, the hEX S announced its port as "ether1" (2026-09-24, twice; the
names cannot be read then, the device takes no login until the run-after-reset script is
done, NUTNO OVĚŘIT). A name therefore only survives both when the profile
carries it, which is why the names a device already has are taken over into the profile
when it is connected (`adopt_device_names`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from mqs.model.inventory import DeviceInventory
from mqs.model.profile import DeviceProfile

# Only ethernet ports are renamed (`/interface ethernet`); wireless is out of scope.
RENAMEABLE_TYPES = frozenset({"ether"})

_DIGITS = re.compile(r"(\d+)")


def natural_key(name: str) -> tuple[tuple[int, int | str], ...]:
    """Sort key that puts ether2 before ether10."""
    return tuple((0, int(part)) if part.isdigit() else (1, part)
                 for part in _DIGITS.split(name) if part)


def device_names(inventory: DeviceInventory) -> dict[str, str]:
    """default-name -> the name the device uses now, for the ports that can be renamed."""
    return {i.default_name: i.name for i in inventory.interfaces
            if i.type in RENAMEABLE_TYPES and i.default_name}


def port_renames(names: Mapping[str, str], inventory: DeviceInventory) -> dict[str, str]:
    """Device name now -> name after the script, for the ports the script renames."""
    return {current: names[default] for default, current in device_names(inventory).items()
            if default in names and names[default] != current}


def names_after(inventory: DeviceInventory, renames: Mapping[str, str]) -> dict[str, str]:
    """Name after the script of each interface, by its name now or by its default-name.

    MNDP can announce a port under an earlier name: on the hEX S (2026-09-24) a bridged
    port renamed in Winbox was still announced as "bridge1/ether1" 13 hours later, while
    a port outside a bridge was announced under its new name 2 s after the rename. A
    reported name is therefore looked up as a present name first, then as a default-name.
    """
    result = {i.default_name: renames.get(i.name, i.name)
              for i in inventory.interfaces if i.default_name}
    result.update({i.name: renames.get(i.name, i.name) for i in inventory.interfaces})
    return result


def final_port_names(profile: DeviceProfile, inventory: DeviceInventory) -> list[str]:
    """Ports selectable as WAN or bridge member, by the names they have after the script."""
    return inventory.renamed(port_renames(profile.interface_names, inventory)).port_names


def renames_to_emit(names: Mapping[str, str],
                    inventory: DeviceInventory | None) -> list[tuple[str, str]]:
    """(default-name, name) pairs the script sets, in port order.

    A name other than the factory one is always set: after a reset the port may have its
    factory name again, whatever the device calls it now. The factory name itself is set
    only to undo a name the device has now. Online, a port the device does not have is
    left out (validation says so); `set` on an empty find would do nothing anyway.
    """
    current = device_names(inventory) if inventory is not None else None
    pairs = []
    for default, name in names.items():
        if current is not None and default not in current:
            continue
        if name != default or (current is not None and current[default] != name):
            pairs.append((default, name))
    return sorted(pairs, key=lambda pair: natural_key(pair[0]))


def _unique(ports: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(ports))


def adopt_device_names(profile: DeviceProfile, inventory: DeviceInventory) -> DeviceProfile:
    """``profile`` fitted to the port names of the connected device.

    The names the device gives its ports are taken over for every port the profile does
    not name itself, and a bridge member or WAN port the profile refers to by its
    default-name, or by a device name the profile changes, is referred to by its final
    name. Returns ``profile`` itself when nothing changes.
    """
    names = dict(profile.interface_names)
    current = device_names(inventory)
    for default, name in current.items():
        if name != default and default not in names:
            names[default] = name

    renames = port_renames(names, inventory)
    finals = {renames.get(i.name, i.name) for i in inventory.interfaces}
    by_default = {i.default_name: renames.get(i.name, i.name)
                  for i in inventory.interfaces if i.default_name}

    def resolve(port: str) -> str:
        if port in finals:
            return port
        return by_default.get(port) or renames.get(port) or port

    ports = _unique(resolve(p) for p in profile.bridge.ports)
    wan = resolve(profile.wan.interface) if profile.wan.interface else profile.wan.interface
    if (names == profile.interface_names and ports == profile.bridge.ports
            and wan == profile.wan.interface):
        return profile
    return profile.model_copy(update={
        "interface_names": names,
        "bridge": profile.bridge.model_copy(update={"ports": ports}),
        "wan": profile.wan.model_copy(update={"interface": wan}),
    })


def rename_port(profile: DeviceProfile, default_name: str, name: str) -> DeviceProfile:
    """``profile`` with the port ``default_name`` called ``name``, references included.

    The port's present name comes from the profile, so the device names must have been
    adopted first (`adopt_device_names`) when a device is connected.
    """
    old = profile.interface_names.get(default_name, default_name)
    ports = _unique(name if p == old else p for p in profile.bridge.ports)
    wan = name if profile.wan.interface == old else profile.wan.interface
    return profile.model_copy(update={
        "interface_names": {**profile.interface_names, default_name: name},
        "bridge": profile.bridge.model_copy(update={"ports": ports}),
        "wan": profile.wan.model_copy(update={"interface": wan}),
    })
