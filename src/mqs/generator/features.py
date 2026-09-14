"""The single table of version-dependent RouterOS facts.

Blocks never compare versions themselves; they ask `TargetContext.has(key)`.
Every entry has a matching row in docs/DIALECT_NOTES.md — change both together.
`verified=False` entries are surfaced as "NUTNO OVĚŘIT" warnings in the preview.
"""

from __future__ import annotations

from dataclasses import dataclass

from mqs.model.version import RosVersion

MIN_SUPPORTED_V6 = (6, 40)


@dataclass(frozen=True)
class Feature:
    key: str
    description: str
    min_v6: tuple[int, ...] | None  # None = not available on v6
    min_v7: tuple[int, ...] | None = (7, 0)  # None = not available on v7
    runtime_probe: bool = False  # must additionally be confirmed on the device
    verified: bool = False


_FEATURES = [
    Feature(
        "interface_list",
        "/interface list and in/out-interface-list matchers",
        min_v6=(6, 41),
    ),
    Feature(
        "bridge_port_hw",
        "hw= on /interface bridge port (bridge HW offload)",
        min_v6=(6, 41),
    ),
    Feature(
        "bridge_mstp",
        "protocol-mode=mstp on /interface bridge (exact first v6 version unknown)",
        min_v6=(6, 41),
    ),
    Feature(
        "ntp_servers_menu",
        "/system ntp client servers menu",
        min_v6=None,
    ),
    Feature(
        "ntp_server_dns_names",
        "server-dns-names= on /system ntp client (v6); first v6 version unknown",
        min_v6=(6, 45),
        min_v7=None,
    ),
    Feature(
        "ipv6_disable_setting",
        "disable-ipv6= on /ipv6 settings; first v7 version unknown",
        min_v6=None,
    ),
    Feature(
        "snmp_ipv6_any_address",
        "addresses=::/0 on /snmp community",
        min_v6=None,
    ),
    Feature(
        "ip_service_api_ssl",
        "api-ssl in /ip service (spec lists it as v7-only; believed present on v6)",
        min_v6=(6, 0),
    ),
    Feature(
        "fasttrack_hw_offload",
        "hw-offload= on fasttrack-connection filter rule",
        min_v6=None,
        runtime_probe=True,
    ),
    Feature(
        "neighbor_discovery_interface_list",
        "discover-interface-list= on /ip neighbor discovery-settings",
        min_v6=(6, 41),
    ),
    Feature(
        "api_plain_login",
        "plain API login (no challenge/response)",
        min_v6=(6, 43),
    ),
    Feature(
        "mac_telnet_ec_srp5",
        "EC-SRP5 authentication for MAC-Telnet",
        min_v6=(6, 43),
    ),
]

FEATURES: dict[str, Feature] = {f.key: f for f in _FEATURES}


def _pad(version: tuple[int, ...]) -> tuple[int, int, int]:
    padded = (*version, 0, 0, 0)
    return padded[0], padded[1], padded[2]


def is_supported_version(version: RosVersion) -> bool:
    if version.major == 6:
        return version.at_least(*MIN_SUPPORTED_V6)
    return version.major == 7


def version_supports(key: str, version: RosVersion) -> bool:
    """True if the version is known to have the feature (runtime probes not considered)."""
    feature = FEATURES[key]
    if version.major == 6:
        minimum = feature.min_v6
    elif version.major == 7:
        minimum = feature.min_v7
    else:
        return False
    if minimum is None:
        return False
    return (version.major, version.minor, version.patch) >= _pad(minimum)
