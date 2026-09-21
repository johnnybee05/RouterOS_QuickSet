"""Builders for test data (inventories, profiles)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from mqs.model.inventory import (
    DeviceInventory,
    InterfaceInfo,
    PackageInfo,
    ResourceInfo,
    ServiceInfo,
    SwitchChip,
)
from mqs.model.presets import apply_preset
from mqs.model.profile import DeviceProfile, Mode
from mqs.model.version import RosVersion

FIVE_PORTS = [f"ether{i}" for i in range(1, 6)]


def make_inventory(
    version: str = "7.15.3",
    ports: Sequence[str] = FIVE_PORTS,
    *,
    port_types: Mapping[str, str] | None = None,
    switches: Mapping[str, str] | None = None,
    port_switch: Mapping[str, str] | None = None,
    packages: Sequence[PackageInfo] = (),
    board_name: str = "test-board",
    services: Sequence[ServiceInfo] = (),
) -> DeviceInventory:
    """``switches`` maps chip name → type, ``port_switch`` maps port → chip name."""
    port_types = port_types or {}
    port_switch = port_switch or {}
    return DeviceInventory(
        resource=ResourceInfo(version=RosVersion.parse(version), board_name=board_name),
        identity="MikroTik",
        interfaces=[
            InterfaceInfo(
                name=p,
                type=port_types.get(p, "ether"),
                default_name=p,
                switch=port_switch.get(p),
            )
            for p in ports
        ],
        switches=[SwitchChip(name=n, type=t) for n, t in (switches or {}).items()],
        packages=list(packages),
        services=list(services),
    )


def router_profile(ports: Sequence[str] = FIVE_PORTS, identity: str = "R1") -> DeviceProfile:
    return apply_preset(DeviceProfile(identity=identity), Mode.ROUTER, ports)
