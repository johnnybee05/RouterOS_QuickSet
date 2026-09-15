"""Local IPv4 network interfaces of this PC."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Interface, IPv4Network
from typing import Any

import ifaddr


@dataclass(frozen=True)
class LocalInterface:
    name: str
    address: IPv4Address
    network: IPv4Network
    index: int | None = None

    @property
    def broadcast(self) -> IPv4Address:
        return self.network.broadcast_address


def list_ipv4_interfaces(
    adapters_func: Callable[[], Iterable[Any]] = ifaddr.get_adapters,
) -> list[LocalInterface]:
    """All non-loopback IPv4 addresses, in adapter order."""
    result: list[LocalInterface] = []
    for adapter in adapters_func():
        for ip in adapter.ips:
            if not isinstance(ip.ip, str):
                continue  # IPv6 entries are tuples in ifaddr
            iface = IPv4Interface(f"{ip.ip}/{ip.network_prefix}")
            if iface.ip.is_loopback:
                continue
            result.append(
                LocalInterface(
                    name=getattr(adapter, "nice_name", "") or adapter.name,
                    address=iface.ip,
                    network=iface.network,
                    index=getattr(adapter, "index", None),
                )
            )
    return result


def interface_for(
    address: IPv4Address, interfaces: Iterable[LocalInterface]
) -> LocalInterface | None:
    return next((i for i in interfaces if address in i.network), None)


def propose_free_address(
    interface: LocalInterface, used: Iterable[IPv4Address] = ()
) -> IPv4Address | None:
    """Highest host address in the PC's subnet that is not ours and not known to be used.

    Callers must still confirm with ARP/ping before using it (MAC bootstrap, spec addendum).
    """
    taken = {interface.address, *used}
    network = interface.network
    if network.prefixlen >= 31:
        return None
    candidate = int(network.broadcast_address) - 1
    while candidate > int(network.network_address):
        address = IPv4Address(candidate)
        if address not in taken:
            return address
        candidate -= 1
    return None
