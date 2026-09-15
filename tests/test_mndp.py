"""MNDP parser and scanner. Packets are synthesised from the documented layout; a real
capture must still confirm the TLV codes (docs/DIALECT_NOTES.md, NUTNO OVĚŘIT)."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network, IPv6Address

import pytest

from mqs.transport.mndp import (
    MNDP_PORT,
    REFRESH_PACKET,
    MndpParseError,
    MndpScanner,
    TlvType,
    _udp_socket,
    decode,
    device_from_packet,
    encode,
    format_mac,
)
from mqs.transport.netifs import LocalInterface

LAN = LocalInterface("Ethernet", IPv4Address("192.168.88.254"), IPv4Network("192.168.88.0/24"))
OTHER = LocalInterface("USB", IPv4Address("10.1.1.5"), IPv4Network("10.1.1.0/24"))


def announcement(mac=b"\x48\x8f\x5a\x01\x02\x03", identity=b"MikroTik", ipv4=b"\xc0\xa8\x58\x01",
                 version=b"7.15.3 (stable)", **extra) -> bytes:
    tlvs = [
        (TlvType.MAC, mac),
        (TlvType.IDENTITY, identity),
        (TlvType.VERSION, version),
        (TlvType.PLATFORM, b"MikroTik"),
        (TlvType.UPTIME, (93784).to_bytes(4, "little")),
        (TlvType.SOFTWARE_ID, b"ABCD-1234"),
        (TlvType.BOARD, b"RB4011iGS+"),
        (TlvType.UNPACK, b"\x00"),
        (TlvType.IPV6, IPv6Address("fe80::4a8f:5aff:fe01:203").packed),
        (TlvType.INTERFACE, b"ether2"),
        (TlvType.IPV4, ipv4),
        (99, b"future"),
    ]
    tlvs += list(extra.items())
    return encode(tlvs, sequence=42)


def test_decode_roundtrip():
    packet = decode(announcement())
    assert packet.sequence == 42
    assert packet.get(TlvType.BOARD) == b"RB4011iGS+"
    assert packet.get(99) == b"future"
    assert packet.get(2) is None


@pytest.mark.parametrize("data", [b"\x00\x00", b"\x00\x00\x00\x00\x00\x01\x00",
                                  b"\x00\x00\x00\x00\x00\x01\x00\x05ab"])
def test_decode_rejects_truncated(data):
    with pytest.raises(MndpParseError):
        decode(data)


def test_device_from_packet():
    device = device_from_packet(decode(announcement()), source_ip="192.168.88.1", local=LAN,
                                now=12.5)
    assert device.mac == "48:8F:5A:01:02:03"
    assert device.identity == "MikroTik"
    assert device.ros_version.major == 7
    assert device.board == "RB4011iGS+"
    assert device.uptime_s == 93784
    assert device.interface_name == "ether2"
    assert device.ipv4 == IPv4Address("192.168.88.1")
    assert device.has_link_local_ipv6
    assert device.local_interface == "Ethernet" and device.local_address == LAN.address
    assert device.last_seen == 12.5
    assert device.unpack == ""


def test_device_requires_mac_and_tolerates_bad_fields():
    assert device_from_packet(decode(REFRESH_PACKET), source_ip="x", local=None, now=0) is None
    odd = encode([(TlvType.MAC, b"\x01\x02\x03\x04\x05\x06"), (TlvType.IPV4, b"\x01"),
                  (TlvType.UPTIME, b"\x01"), (TlvType.VERSION, b"garbage")])
    device = device_from_packet(decode(odd), source_ip="10.0.0.9", local=None, now=0)
    assert device.ipv4 is None and device.uptime_s is None and device.ipv6 is None
    assert device.ros_version is None and not device.has_link_local_ipv6
    assert device.local_interface == "" and device.local_address is None
    assert format_mac(b"\x00\x0a") == "00:0A"


class FakeSocket:
    def __init__(self, address: str) -> None:
        self.address = address
        self.inbox: list[tuple[bytes, tuple[str, int]] | BaseException] = []
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False
        self.fail_send = False

    def sendto(self, data, target):
        if self.fail_send:
            raise OSError("network unreachable")
        self.sent.append((data, target))

    def recvfrom(self, size):
        if not self.inbox:
            raise BlockingIOError
        item = self.inbox.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed = True


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def make_scanner(interfaces, fail=()):
    sockets: dict[str, FakeSocket] = {}

    def factory(address):
        if address in fail:
            raise OSError("address in use")
        sockets[address] = FakeSocket(address)
        return sockets[address]

    def fake_select(read, write, error, timeout):
        return [s for s in read if s.inbox], [], []

    clock = Clock()
    scanner = MndpScanner(interfaces, socket_factory=factory, select_func=fake_select, clock=clock)
    return scanner, sockets, clock


def test_scanner_per_interface_sockets_and_solicit():
    scanner, sockets, _ = make_scanner([LAN, OTHER])
    with scanner:
        assert scanner.listening and set(sockets) == {"192.168.88.254", "10.1.1.5"}
        scanner.solicit()
        assert sockets["192.168.88.254"].sent == [
            (REFRESH_PACKET, ("192.168.88.255", MNDP_PORT)),
            (REFRESH_PACKET, ("255.255.255.255", MNDP_PORT)),
        ]
    assert all(s.closed for s in sockets.values())
    assert not scanner.listening


def test_scanner_poll_updates_devices():
    scanner, sockets, clock = make_scanner([LAN, OTHER])
    scanner.open()
    lan = sockets["192.168.88.254"]
    lan.inbox += [
        (REFRESH_PACKET, ("192.168.88.254", MNDP_PORT)),  # our own solicit echoed back
        (b"\x01", ("192.168.88.7", MNDP_PORT)),  # garbage
        (announcement(identity=b"zeta"), ("192.168.88.1", MNDP_PORT)),
        (announcement(mac=b"\x00\x00\x00\x00\x00\x09", identity=b"Alpha"),
         ("192.168.88.2", MNDP_PORT)),
        ConnectionResetError("WSAECONNRESET"),
        (announcement(identity=b"zeta-after-reset"), ("192.168.88.1", MNDP_PORT)),
    ]
    updated = scanner.poll(0.1)
    # A socket error stops draining this socket for now; nothing queued after it is lost.
    assert [d.identity for d in updated] == ["zeta", "Alpha"]
    assert [d.identity for d in scanner.devices()] == ["Alpha", "zeta"]
    assert updated[0].local_interface == "Ethernet"

    clock.now += 60
    sockets["10.1.1.5"].inbox.append(
        (announcement(mac=b"\x00\x00\x00\x00\x00\x09", identity=b"Alpha-renamed",
                      ipv4=b"\x0a\x01\x01\x01"), ("10.1.1.1", MNDP_PORT)))
    updated = scanner.poll(0.1)
    assert sorted(d.identity for d in updated) == ["Alpha-renamed", "zeta-after-reset"]
    assert [d.identity for d in scanner.devices(max_age=30)] == [
        "Alpha-renamed", "zeta-after-reset"]
    assert len(scanner.devices()) == 2  # same MACs, entries replaced

    clock.now += 60
    assert scanner.poll(0.1) == []
    assert scanner.devices(max_age=30) == []


def test_real_udp_socket_factory():
    try:
        sock = _udp_socket("127.0.0.1")
    except OSError as exc:  # port 5678 held by Winbox or another scanner
        pytest.skip(f"cannot bind MNDP port: {exc}")
    try:
        assert sock.getsockname() == ("127.0.0.1", MNDP_PORT)
        assert sock.gettimeout() == 0.0
    finally:
        sock.close()
    with pytest.raises(OSError):
        _udp_socket("203.0.113.77")  # not a local address


def test_scanner_wildcard_fallback_and_errors():
    scanner, sockets, _ = make_scanner([LAN], fail={"192.168.88.254"})
    scanner.open()
    assert set(sockets) == {"0.0.0.0"}
    assert "address in use" in scanner.errors[0]
    sockets["0.0.0.0"].inbox.append((announcement(), ("192.168.88.1", MNDP_PORT)))
    device = scanner.poll(0.1)[0]
    assert device.local_interface == "Ethernet"  # resolved from the source address
    sockets["0.0.0.0"].fail_send = True
    scanner.solicit()
    assert scanner.errors[-1].startswith("solicit 255.255.255.255")

    dead, _, _ = make_scanner([LAN], fail={"192.168.88.254", "0.0.0.0"})
    dead.open()
    assert not dead.listening and len(dead.errors) == 2
    assert dead.poll(0.1) == []
