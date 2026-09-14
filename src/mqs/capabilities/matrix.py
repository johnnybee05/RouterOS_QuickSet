"""Capability matrix built from what the device reports (or from offline assumptions)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from mqs.model.inventory import DeviceInventory, InterfaceInfo
from mqs.model.version import RosVersion


class PortKind(StrEnum):
    ETHERNET = "ethernet"
    SFP = "sfp"
    WIRELESS = "wireless"
    BONDING = "bonding"
    VLAN = "vlan"
    OTHER = "other"


# Name prefixes RouterOS uses for SFP-type ethernet ports; NUTNO OVĚŘIT for combo ports.
_SFP_PREFIXES = ("sfp", "qsfp", "combo")


def classify_port(iface: InterfaceInfo) -> PortKind:
    if iface.type == "ether":
        name = (iface.default_name or iface.name).lower()
        return PortKind.SFP if name.startswith(_SFP_PREFIXES) else PortKind.ETHERNET
    if iface.type in ("wlan", "wifi"):
        return PortKind.WIRELESS
    if iface.type == "bond":
        return PortKind.BONDING
    if iface.type == "vlan":
        return PortKind.VLAN
    return PortKind.OTHER


@dataclass(frozen=True)
class PortCapability:
    name: str
    type: str
    kind: PortKind
    switch: str | None = None
    chip_type: str | None = None


@dataclass(frozen=True)
class Capabilities:
    version: RosVersion
    board_name: str = ""
    architecture: str = ""
    ports: dict[str, PortCapability] = field(default_factory=dict)
    chips: dict[str, str] = field(default_factory=dict)  # switch name -> chip type

    @classmethod
    def from_inventory(cls, inventory: DeviceInventory) -> Capabilities:
        chips = {s.name: s.type for s in inventory.switches}
        ports = {
            i.name: PortCapability(
                name=i.name,
                type=i.type,
                kind=classify_port(i),
                switch=i.switch,
                chip_type=chips.get(i.switch) if i.switch else None,
            )
            for i in inventory.interfaces
        }
        return cls(
            version=inventory.version,
            board_name=inventory.resource.board_name,
            architecture=inventory.resource.architecture,
            ports=ports,
            chips=chips,
        )

    @classmethod
    def offline(
        cls,
        version: str | RosVersion,
        ports: Sequence[str],
        *,
        switches: Mapping[str, str] | None = None,
        port_switch: Mapping[str, str] | None = None,
        port_types: Mapping[str, str] | None = None,
    ) -> Capabilities:
        """Build from manually entered data (no device). Unknown mapping = no switch chip."""
        if isinstance(version, str):
            version = RosVersion.parse(version)
        switches = dict(switches or {})
        port_switch = port_switch or {}
        port_types = port_types or {}
        result = {}
        for name in ports:
            iface = InterfaceInfo(
                name=name, type=port_types.get(name, "ether"), switch=port_switch.get(name)
            )
            result[name] = PortCapability(
                name=name,
                type=iface.type,
                kind=classify_port(iface),
                switch=iface.switch,
                chip_type=switches.get(iface.switch) if iface.switch else None,
            )
        return cls(version=version, ports=result, chips=switches)

    def port(self, name: str) -> PortCapability:
        """Capability of a port; unknown names are treated as ethernet without a switch chip."""
        return self.ports.get(name) or PortCapability(name=name, type="", kind=PortKind.OTHER)
