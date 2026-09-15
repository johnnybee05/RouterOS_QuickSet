"""Cross-field validation of a DeviceProfile.

Structural errors (bad IP syntax, out-of-range numbers) are raised by pydantic on
construction. Everything that depends on several fields or on the detected device is
reported here as a list of issues, so the UI can show all problems at once.
"""

from __future__ import annotations

import re
from collections import Counter
from ipaddress import IPv4Interface

from mqs.issues import Issue, Severity
from mqs.model.bridge import LanAddressing
from mqs.model.inventory import DeviceInventory
from mqs.model.network import usable_host
from mqs.model.profile import DeviceProfile, Mode
from mqs.model.wan import PppoeWan, StaticWan

# Conservative charset; real RouterOS rules are NUTNO OVĚŘIT (docs/DIALECT_NOTES.md).
IDENTITY_RE = re.compile(r"^[A-Za-z0-9._-]+$")
IDENTITY_MAX_LEN = 64
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

E, W, INFO = Severity.ERROR, Severity.WARNING, Severity.INFO


def validate_profile(
    profile: DeviceProfile, inventory: DeviceInventory | None = None
) -> list[Issue]:
    issues: list[Issue] = []
    issues += _identity(profile)
    issues += _wan(profile, inventory)
    issues += _bridge(profile, inventory)
    issues += _lan(profile)
    issues += _firewall(profile)
    issues += _mode(profile)
    return issues


def _identity(profile: DeviceProfile) -> list[Issue]:
    identity = profile.identity
    if not identity:
        return [Issue("identity.empty", E, "identity")]
    issues = []
    if len(identity) > IDENTITY_MAX_LEN:
        issues.append(Issue("identity.too_long", E, "identity", {"max": IDENTITY_MAX_LEN}))
    if not IDENTITY_RE.match(identity):
        issues.append(Issue("identity.invalid_chars", E, "identity"))
    return issues


def _wan(profile: DeviceProfile, inventory: DeviceInventory | None) -> list[Issue]:
    wan = profile.wan
    if not wan.enabled:
        return []
    issues: list[Issue] = []
    if not wan.interface:
        issues.append(Issue("wan.no_interface", E, "wan.interface"))
    else:
        params = {"interface": wan.interface}
        if wan.interface in profile.bridge.ports:
            issues.append(Issue("wan.interface_in_bridge", E, "wan.interface", params))
        if inventory is not None and wan.interface not in inventory.port_names:
            issues.append(Issue("wan.interface_unknown", E, "wan.interface", params))

    settings = wan.settings
    if isinstance(settings, StaticWan):
        address = settings.address
        if not usable_host(address):
            issues.append(
                Issue("wan.static.address_not_host", E, "wan.settings.address",
                      {"address": str(address)})
            )
        if settings.gateway == address.ip:
            issues.append(Issue("wan.static.gateway_is_address", E, "wan.settings.gateway"))
        elif settings.gateway not in address.network:
            issues.append(
                Issue("wan.static.gateway_outside_subnet", E, "wan.settings.gateway",
                      {"gateway": str(settings.gateway), "network": str(address.network)})
            )
    elif isinstance(settings, PppoeWan):
        if not settings.username:
            issues.append(Issue("wan.pppoe.username_empty", E, "wan.settings.username"))
        if not settings.password.get_secret_value():
            issues.append(Issue("wan.pppoe.password_empty", E, "wan.settings.password"))
        if not NAME_RE.match(settings.interface_name):
            issues.append(
                Issue("wan.pppoe.interface_name_invalid", E, "wan.settings.interface_name")
            )
    return issues


def _bridge(profile: DeviceProfile, inventory: DeviceInventory | None) -> list[Issue]:
    bridge = profile.bridge
    issues: list[Issue] = []
    if not NAME_RE.match(bridge.name):
        issues.append(Issue("bridge.name_invalid", E, "bridge.name"))
    for port, count in Counter(bridge.ports).items():
        if count > 1:
            issues.append(Issue("bridge.port_duplicate", E, "bridge.ports", {"interface": port}))
    if inventory is not None:
        known = set(inventory.port_names)
        for port in dict.fromkeys(bridge.ports):
            if port not in known:
                issues.append(
                    Issue("bridge.port_unknown", E, "bridge.ports", {"interface": port})
                )
    if not bridge.ports:
        issues.append(Issue("bridge.no_ports", W, "bridge.ports"))
    return issues


def _lan(profile: DeviceProfile) -> list[Issue]:
    lan = profile.lan
    issues: list[Issue] = []
    address = lan.address if lan.addressing == LanAddressing.STATIC else None

    if lan.addressing == LanAddressing.STATIC:
        if lan.address is None:
            issues.append(Issue("lan.address_missing", E, "lan.address"))
        elif not usable_host(lan.address):
            issues.append(
                Issue("lan.address_not_host", E, "lan.address", {"address": str(lan.address)})
            )
        wan_settings = profile.wan.settings
        if (
            lan.address is not None
            and profile.wan.enabled
            and isinstance(wan_settings, StaticWan)
            and lan.address.network.overlaps(wan_settings.address.network)
        ):
            issues.append(
                Issue("lan.overlaps_wan", E, "lan.address",
                      {"lan": str(lan.address.network), "wan": str(wan_settings.address.network)})
            )

    issues += _lan_gateway(profile, address)

    dhcp = lan.dhcp_server
    if not dhcp.enabled:
        return issues
    if address is None:
        issues.append(Issue("dhcp.requires_static", E, "lan.dhcp_server.enabled"))
        return issues

    network = address.network
    pool_params = {"pool": str(dhcp.pool), "network": str(network), "address": str(address.ip)}
    if not dhcp.pool.within(network):
        issues.append(Issue("dhcp.pool_outside_subnet", E, "lan.dhcp_server.pool", pool_params))
    if address.ip in dhcp.pool:
        issues.append(Issue("dhcp.pool_contains_address", E, "lan.dhcp_server.pool", pool_params))
    if network.prefixlen < 31 and (
        network.network_address in dhcp.pool or network.broadcast_address in dhcp.pool
    ):
        issues.append(Issue("dhcp.pool_contains_reserved", E, "lan.dhcp_server.pool", pool_params))
    if dhcp.gateway is not None and dhcp.gateway not in network:
        issues.append(
            Issue("dhcp.gateway_outside_subnet", E, "lan.dhcp_server.gateway",
                  {"gateway": str(dhcp.gateway), "network": str(network)})
        )
    return issues


def _lan_gateway(profile: DeviceProfile, address: IPv4Interface | None) -> list[Issue]:
    """Default route through the LAN gateway, used by devices without a WAN of their own."""
    gateway = profile.lan.gateway
    if gateway is None:
        return []
    if profile.lan.addressing != LanAddressing.STATIC:
        return [Issue("lan.gateway_requires_static", W, "lan.gateway")]

    issues: list[Issue] = []
    if address is not None:
        if gateway == address.ip:
            issues.append(Issue("lan.gateway_is_address", E, "lan.gateway"))
        elif gateway not in address.network:
            issues.append(
                Issue("lan.gateway_outside_subnet", E, "lan.gateway",
                      {"gateway": str(gateway), "network": str(address.network)})
            )
    if _wan_adds_default_route(profile):
        issues.append(Issue("lan.gateway_conflicts_wan", W, "lan.gateway"))
    return issues


def _wan_adds_default_route(profile: DeviceProfile) -> bool:
    """True when the WAN already installs a default route of its own."""
    wan = profile.wan
    if not (wan.enabled and wan.interface):
        return False
    settings = wan.settings
    if isinstance(settings, StaticWan):
        return True
    return settings.add_default_route


def _firewall(profile: DeviceProfile) -> list[Issue]:
    fasttrack = profile.firewall.fasttrack
    if not fasttrack.enabled:
        return []
    issues: list[Issue] = []
    if not fasttrack.no_shaping_confirmed:
        issues.append(
            Issue("firewall.fasttrack_shaping_unconfirmed", W, "firewall.fasttrack.enabled")
        )
    if profile.wan.enabled and isinstance(profile.wan.settings, PppoeWan):
        issues.append(Issue("firewall.fasttrack_pppoe_no_hw", INFO, "firewall.fasttrack.enabled"))
    return issues


def _mode(profile: DeviceProfile) -> list[Issue]:
    if profile.mode == Mode.ROUTER and not profile.wan.enabled:
        return [Issue("mode.router_without_wan", W, "mode")]
    return []
