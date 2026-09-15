"""MikroTik Neighbor Discovery Protocol (MNDP, UDP 5678) — find devices before connecting.

Packet layout as understood from public dissectors (NUTNO OVĚŘIT against a real capture,
see docs/DIALECT_NOTES.md): 2 bytes header, 2 bytes sequence number (big endian), then
TLVs of 2 bytes type + 2 bytes length (big endian) + value. The type codes live only in
`TlvType`; the parser is pure and the socket layer is a thin injectable wrapper.
"""

from __future__ import annotations

import select
import socket
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import IntEnum
from ipaddress import IPv4Address, IPv6Address
from typing import Any

from mqs.model.version import RosVersion
from mqs.transport.netifs import LocalInterface, interface_for

MNDP_PORT = 5678
HEADER_LEN = 4
# Sending an empty MNDP header asks devices to announce themselves (NUTNO OVĚŘIT).
REFRESH_PACKET = b"\x00\x00\x00\x00"
GLOBAL_BROADCAST = "255.255.255.255"


class TlvType(IntEnum):
    MAC = 1
    IDENTITY = 5
    VERSION = 7
    PLATFORM = 8
    UPTIME = 10  # 4 bytes, seconds, little endian (NUTNO OVĚŘIT)
    SOFTWARE_ID = 11
    BOARD = 12
    UNPACK = 14
    IPV6 = 15
    INTERFACE = 16
    IPV4 = 17


class MndpParseError(ValueError):
    pass


@dataclass(frozen=True)
class MndpPacket:
    header: bytes
    sequence: int
    tlvs: tuple[tuple[int, bytes], ...]

    def get(self, tlv_type: int) -> bytes | None:
        return next((v for t, v in self.tlvs if t == tlv_type), None)


def decode(data: bytes) -> MndpPacket:
    if len(data) < HEADER_LEN:
        raise MndpParseError("packet shorter than header")
    tlvs: list[tuple[int, bytes]] = []
    offset = HEADER_LEN
    while offset < len(data):
        if offset + 4 > len(data):
            raise MndpParseError("truncated TLV header")
        tlv_type = int.from_bytes(data[offset:offset + 2], "big")
        length = int.from_bytes(data[offset + 2:offset + 4], "big")
        start, end = offset + 4, offset + 4 + length
        if end > len(data):
            raise MndpParseError("truncated TLV value")
        tlvs.append((tlv_type, data[start:end]))
        offset = end
    return MndpPacket(data[:2], int.from_bytes(data[2:4], "big"), tuple(tlvs))


def encode(tlvs: Iterable[tuple[int, bytes]], *, header: bytes = b"\x00\x00",
           sequence: int = 0) -> bytes:
    """Build a packet (used by tests and simulators)."""
    body = b"".join(t.to_bytes(2, "big") + len(v).to_bytes(2, "big") + v for t, v in tlvs)
    return header[:2].ljust(2, b"\x00") + sequence.to_bytes(2, "big") + body


def format_mac(raw: bytes) -> str:
    return ":".join(f"{b:02X}" for b in raw)


@dataclass(frozen=True)
class DiscoveredDevice:
    mac: str
    identity: str = ""
    version: str = ""
    platform: str = ""
    board: str = ""
    uptime_s: int | None = None
    software_id: str = ""
    unpack: str = ""
    interface_name: str = ""  # device-side interface the announcement left from
    ipv4: IPv4Address | None = None
    ipv6: IPv6Address | None = None
    source_ip: str = ""
    local_interface: str = ""  # PC NIC it arrived on
    local_address: IPv4Address | None = None
    last_seen: float = 0.0

    @property
    def ros_version(self) -> RosVersion | None:
        try:
            return RosVersion.parse(self.version)
        except ValueError:
            return None

    @property
    def has_link_local_ipv6(self) -> bool:
        return self.ipv6 is not None and self.ipv6.is_link_local


def _text(value: bytes | None) -> str:
    return value.decode("utf-8", errors="replace").strip("\x00").strip() if value else ""


def device_from_packet(
    packet: MndpPacket,
    *,
    source_ip: str,
    local: LocalInterface | None,
    now: float,
) -> DiscoveredDevice | None:
    mac = packet.get(TlvType.MAC)
    if mac is None or len(mac) != 6:
        return None  # e.g. another scanner's refresh packet
    uptime = packet.get(TlvType.UPTIME)
    ipv4 = packet.get(TlvType.IPV4)
    ipv6 = packet.get(TlvType.IPV6)
    return DiscoveredDevice(
        mac=format_mac(mac),
        identity=_text(packet.get(TlvType.IDENTITY)),
        version=_text(packet.get(TlvType.VERSION)),
        platform=_text(packet.get(TlvType.PLATFORM)),
        board=_text(packet.get(TlvType.BOARD)),
        uptime_s=int.from_bytes(uptime, "little") if uptime and len(uptime) == 4 else None,
        software_id=_text(packet.get(TlvType.SOFTWARE_ID)),
        unpack=_text(packet.get(TlvType.UNPACK)),
        interface_name=_text(packet.get(TlvType.INTERFACE)),
        ipv4=IPv4Address(ipv4) if ipv4 and len(ipv4) == 4 else None,
        ipv6=IPv6Address(ipv6) if ipv6 and len(ipv6) == 16 else None,
        source_ip=source_ip,
        local_interface=local.name if local else "",
        local_address=local.address if local else None,
        last_seen=now,
    )


def _udp_socket(local_address: str) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind((local_address, MNDP_PORT))
        sock.setblocking(False)
    except OSError:
        sock.close()
        raise
    return sock


class MndpScanner:
    """Listens on every local IPv4 interface; devices are keyed by MAC address."""

    def __init__(
        self,
        interfaces: Sequence[LocalInterface],
        *,
        socket_factory: Callable[[str], Any] = _udp_socket,
        select_func: Callable[..., Any] = select.select,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.interfaces = list(interfaces)
        self._socket_factory = socket_factory
        self._select = select_func
        self._clock = clock
        self._sockets: dict[Any, LocalInterface | None] = {}
        self._devices: dict[str, DiscoveredDevice] = {}
        self.errors: list[str] = []

    def open(self) -> None:
        for iface in self.interfaces:
            try:
                self._sockets[self._socket_factory(str(iface.address))] = iface
            except OSError as exc:
                self.errors.append(f"{iface.name} {iface.address}: {exc}")
        if not self._sockets:
            try:
                self._sockets[self._socket_factory("0.0.0.0")] = None
            except OSError as exc:  # port held by another program, firewall, ...
                self.errors.append(f"0.0.0.0:{MNDP_PORT}: {exc}")

    @property
    def listening(self) -> bool:
        return bool(self._sockets)

    def solicit(self) -> None:
        for sock, iface in self._sockets.items():
            targets = {GLOBAL_BROADCAST}
            if iface is not None:
                targets.add(str(iface.broadcast))
            for target in sorted(targets):
                try:
                    sock.sendto(REFRESH_PACKET, (target, MNDP_PORT))
                except OSError as exc:
                    self.errors.append(f"solicit {target}: {exc}")

    def poll(self, timeout: float) -> list[DiscoveredDevice]:
        """Wait up to `timeout` seconds; return devices announced during this poll."""
        if not self._sockets:
            return []
        ready, _, _ = self._select(list(self._sockets), [], [], timeout)
        updated: list[DiscoveredDevice] = []
        for sock in ready:
            while True:
                try:
                    data, (source_ip, _port) = sock.recvfrom(2048)
                except (BlockingIOError, InterruptedError):
                    break
                except OSError:
                    break  # e.g. WSAECONNRESET after an ICMP port unreachable
                try:
                    packet = decode(data)
                except MndpParseError:
                    continue
                local = self._sockets[sock]
                if local is None:
                    local = interface_for(IPv4Address(source_ip), self.interfaces)
                device = device_from_packet(packet, source_ip=source_ip, local=local,
                                            now=self._clock())
                if device is not None:
                    self._devices[device.mac] = device
                    updated.append(device)
        return updated

    def devices(self, max_age: float | None = None) -> list[DiscoveredDevice]:
        now = self._clock()
        found = [d for d in self._devices.values()
                 if max_age is None or now - d.last_seen <= max_age]
        return sorted(found, key=lambda d: (d.identity.lower(), int(d.ipv4 or 0), d.mac))

    def close(self) -> None:
        for sock in self._sockets:
            sock.close()
        self._sockets.clear()

    def __enter__(self) -> MndpScanner:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
