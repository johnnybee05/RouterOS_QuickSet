"""Local IPv4 network interfaces of this PC."""

from __future__ import annotations

import ctypes
import socket
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


class _AdapterAddresses(ctypes.Structure):
    """Head of the Win32 IP_ADAPTER_ADDRESSES_LH, up to the physical address."""


_AdapterAddresses._fields_ = [
    ("Length", ctypes.c_ulong),
    ("IfIndex", ctypes.c_ulong),
    ("Next", ctypes.POINTER(_AdapterAddresses)),
    ("AdapterName", ctypes.c_char_p),
    ("FirstUnicastAddress", ctypes.c_void_p),
    ("FirstAnycastAddress", ctypes.c_void_p),
    ("FirstMulticastAddress", ctypes.c_void_p),
    ("FirstDnsServerAddress", ctypes.c_void_p),
    ("DnsSuffix", ctypes.c_wchar_p),
    ("Description", ctypes.c_wchar_p),
    ("FriendlyName", ctypes.c_wchar_p),
    ("PhysicalAddress", ctypes.c_ubyte * 8),
    ("PhysicalAddressLength", ctypes.c_ulong),
]
_ERROR_BUFFER_OVERFLOW = 111


def interface_mac(index: int | None) -> bytes | None:
    """Hardware address of the adapter with this interface index, or None.

    MAC-Telnet needs the address of the NIC the request leaves from, because the device
    answers the source MAC carried in the payload. uuid.getnode() is no substitute: on a
    multi-adapter machine it returns a random locally-administered address, which the
    device silently ignores (verified against RouterOS 7.20.7 on 2026-09-16).
    """
    if index is None or not hasattr(ctypes, "windll"):
        return None
    size = ctypes.c_ulong(15000)
    for _ in range(2):
        buffer = ctypes.create_string_buffer(size.value)
        result = ctypes.windll.iphlpapi.GetAdaptersAddresses(
            0, 0, None, ctypes.byref(buffer), ctypes.byref(size))
        if result == 0:
            node = ctypes.cast(buffer, ctypes.POINTER(_AdapterAddresses))
            while node:
                adapter = node.contents
                if adapter.IfIndex == index and adapter.PhysicalAddressLength == 6:
                    return bytes(adapter.PhysicalAddress[:6])
                node = adapter.Next
            return None
        if result != _ERROR_BUFFER_OVERFLOW:
            return None
    return None


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


def route_source_address(host: str) -> str:
    """Local IPv4 address the OS would use to reach `host` (UDP connect sends nothing)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((host, 9))
        return sock.getsockname()[0]


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
