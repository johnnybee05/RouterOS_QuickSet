"""Builders for test data (inventories, profiles)."""

from __future__ import annotations

import struct
from collections.abc import Mapping, Sequence

from mqs.model.inventory import (
    DeviceInventory,
    InterfaceInfo,
    PackageInfo,
    ResourceInfo,
    RouterboardInfo,
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
    default_names: Mapping[str, str] | None = None,
    architecture: str = "",
    free_hdd_space: int = 0,
    free_memory: int = 0,
    has_flash_dir: bool = False,
    factory_firmware: str = "",
    factory_software: str = "",
) -> DeviceInventory:
    """``switches`` maps chip name → type, ``port_switch`` maps port → chip name,
    ``default_names`` a renamed port → its default-name (others are not renamed)."""
    port_types = port_types or {}
    port_switch = port_switch or {}
    default_names = default_names or {}
    return DeviceInventory(
        resource=ResourceInfo(version=RosVersion.parse(version), board_name=board_name,
                              architecture=architecture, free_hdd_space=free_hdd_space,
                              free_memory=free_memory, factory_software=factory_software),
        routerboard=RouterboardInfo(routerboard=True, model=board_name,
                                    factory_firmware=factory_firmware,
                                    current_firmware=version, upgrade_firmware=version)
        if factory_firmware else None,
        has_flash_dir=has_flash_dir,
        identity="MikroTik",
        interfaces=[
            InterfaceInfo(
                name=p,
                type=port_types.get(p, "ether"),
                default_name=default_names.get(p, p),
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


def make_npk(name: str = "system", version: tuple[int, int, int] = (7, 23, 7),
             arch: str = "arm", *, kind: bytes = b"f", channel: str = "long-term",
             bundle: bool = False) -> bytes:
    """A RouterOS package header laid out like MikroTik's own (test_update.REAL_ARM_HEADER
    and REAL_MMIPS_V6_HEADER check the two against each other), for any name, version and
    architecture. ``bundle`` lays it out the way a v6 package is built."""
    major, minor, revision = version
    info = name.encode().ljust(16, b"\0") + bytes([revision]) + kind + bytes([minor, major])
    info += struct.pack("<I", 0x6AAA8943) + b"\0" * 12

    def part(kind_id: int, data: bytes) -> bytes:
        return struct.pack("<HI", kind_id, len(data)) + data

    if bundle:
        # A v6 package: its own info in part 18, then a part 1 for each package in it.
        head = part(25, b"") + part(18, info) + part(24, channel.encode())
        head += part(16, arch.encode()) + part(2, b"RouterOS for devices")
        for member in ("system", "ipv6", "wireless@"):
            member_info = member.encode().ljust(16, b"\0") + bytes([revision]) + kind
            member_info += bytes([minor, major]) + struct.pack("<I", 0x6AAA8943) + b"\0" * 12
            head += part(1, member_info) + part(16, arch.encode())
        return struct.pack("<II", 0xBAD0F11E, len(head)) + head
    parts = (part(25, b"") + part(1, info) + part(24, channel.encode())
             + part(16, arch.encode()) + part(2, b"Main package") + part(4, b"\0" * 64))
    return struct.pack("<II", 0xBAD0F11E, len(parts)) + parts
