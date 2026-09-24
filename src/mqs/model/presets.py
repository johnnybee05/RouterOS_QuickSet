"""Predefined scenarios. They only pre-fill the form; nothing else depends on the mode."""

from __future__ import annotations

from collections.abc import Sequence

from mqs.model.bridge import (
    DEFAULT_LAN_ADDRESS,
    BridgeConfig,
    DhcpServerConfig,
    LanAddressing,
    LanConfig,
)
from mqs.model.firewall import FirewallConfig
from mqs.model.profile import DeviceProfile, Mode
from mqs.model.wan import DhcpWan, WanConfig


def apply_preset(profile: DeviceProfile, mode: Mode, ports: Sequence[str]) -> DeviceProfile:
    """Return a copy of the profile pre-filled for the scenario.

    Identity, profile name and the services/hardening block are kept.
    ``ports`` is the selectable port list in device order; the first one becomes WAN
    in the Router scenario (the user can pick any other port afterwards).
    """
    if mode == Mode.ROUTER:
        wan_port = ports[0] if ports else None
        wan = WanConfig(
            enabled=True,
            interface=wan_port,
            settings=DhcpWan(),
            add_to_wan_list=True,
            mss_clamping=False,
            nat_masquerade=True,
        )
        bridge = BridgeConfig(ports=[p for p in ports if p != wan_port])
        lan = LanConfig(
            addressing=LanAddressing.STATIC,
            address=DEFAULT_LAN_ADDRESS,
            dhcp_server=DhcpServerConfig(enabled=True),
        )
        firewall = FirewallConfig(basic_filter=True)
    elif mode == Mode.BRIDGE_AP:
        wan = _disabled_wan()
        bridge = BridgeConfig(ports=list(ports))
        lan = LanConfig(
            addressing=LanAddressing.DHCP_CLIENT,
            address=None,
            dhcp_server=DhcpServerConfig(enabled=False),
        )
        firewall = FirewallConfig(basic_filter=False)
    else:
        wan = _disabled_wan()
        bridge = BridgeConfig()
        lan = LanConfig(
            addressing=LanAddressing.NONE,
            address=None,
            dhcp_server=DhcpServerConfig(enabled=False),
        )
        firewall = FirewallConfig(basic_filter=False)

    profile = profile.model_copy(
        update={"mode": mode, "wan": wan, "bridge": bridge, "lan": lan, "firewall": firewall}
    )
    return profile.with_basic_filter(firewall.basic_filter)


def _disabled_wan() -> WanConfig:
    return WanConfig(enabled=False, interface=None, add_to_wan_list=False, nat_masquerade=False)
