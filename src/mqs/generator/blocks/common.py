"""Helpers shared by generator blocks."""

from __future__ import annotations

from typing import Literal

from mqs.generator.context import TargetContext
from mqs.model.bridge import LanAddressing
from mqs.model.profile import DeviceProfile
from mqs.model.wan import PppoeWan

WAN_LIST = "WAN"
LAN_LIST = "LAN"
MASKED_SECRET = "********"

Direction = Literal["in", "out"]


def bridge_enabled(profile: DeviceProfile) -> bool:
    return bool(profile.bridge.ports) or profile.lan.addressing != LanAddressing.NONE


def wan_port(profile: DeviceProfile) -> str | None:
    """Physical WAN port, or None when WAN is not configured."""
    wan = profile.wan
    return wan.interface if wan.enabled and wan.interface else None


def wan_vlan_interface(profile: DeviceProfile) -> str | None:
    """VLAN interface of a tagged WAN (Cetin PPPoE: vlan848), None when it is untagged."""
    return profile.wan.vlan_interface if wan_port(profile) is not None else None


def wan_link(profile: DeviceProfile) -> str | None:
    """Interface the WAN client attaches to: the VLAN interface when tagged, else the port.

    This is what `/ip dhcp-client`, `/ip address` and `/interface pppoe-client` get as
    their `interface=`; a tagged WAN leaves the physical port without any address.
    """
    port = wan_port(profile)
    if port is None:
        return None
    return profile.wan.vlan_interface or port


def wan_l3_interface(profile: DeviceProfile) -> str | None:
    """Interface carrying WAN IP traffic: the PPPoE client for PPPoE, the link otherwise."""
    link = wan_link(profile)
    if link is None:
        return None
    settings = profile.wan.settings
    return settings.interface_name if isinstance(settings, PppoeWan) else link


def uses_wan_list(profile: DeviceProfile, ctx: TargetContext) -> bool:
    return wan_port(profile) is not None and profile.wan.add_to_wan_list and ctx.has(
        "interface_list"
    )


def uses_lan_list(profile: DeviceProfile, ctx: TargetContext) -> bool:
    return bridge_enabled(profile) and ctx.has("interface_list")


def wan_match(profile: DeviceProfile, ctx: TargetContext, direction: Direction) -> dict[str, str]:
    """Matcher for WAN traffic; interface list when available, else the interface itself."""
    if uses_wan_list(profile, ctx):
        return {f"{direction}-interface-list": WAN_LIST}
    iface = wan_l3_interface(profile)
    return {f"{direction}-interface": iface} if iface else {}


def lan_match(profile: DeviceProfile, ctx: TargetContext, direction: Direction) -> dict[str, str]:
    if not bridge_enabled(profile):
        return {}
    if uses_lan_list(profile, ctx):
        return {f"{direction}-interface-list": LAN_LIST}
    return {f"{direction}-interface": profile.bridge.name}


def format_duration(seconds: int) -> str:
    """RouterOS time value, e.g. 1800 -> '30m', 90061 -> '1d1h1m1s'. Syntax NUTNO OVĚŘIT."""
    if seconds <= 0:
        return "0s"
    parts = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        value, seconds = divmod(seconds, size)
        if value:
            parts.append(f"{value}{unit}")
    return "".join(parts)
