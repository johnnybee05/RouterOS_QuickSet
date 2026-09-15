"""MAC-Telnet packet and control-packet encoding (UDP 20561).

Layout follows MarginResearch/mikrotik_authentication (Apache-2.0); see THIRD_PARTY_NOTICES.md.
NUTNO OVĚŘIT against a capture from a real device.

Outer packet header (22 bytes, big-endian):
    version(1) type(1) src_mac(6) dst_mac(6) session_id(2) client_type(2) byte_counter(4)
Control packet header (9 bytes): magic(4) type(1, signed) length(4, big-endian) + data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from struct import pack, unpack

MACTELNET_PORT = 20561
HEADER_LEN = 0x16  # 22
PROTOCOL_VERSION = 1
CLIENT_TYPE = 0x15  # 21, "MAC Telnet"
CONTROL_MAGIC = b"\x56\x34\x12\xff"


class MessageType(IntEnum):
    START_SESSION = 0
    DATA = 1
    ACKNOWLEDGE = 2
    END_SESSION = 255


class ControlType(IntEnum):
    BEGIN_AUTHENTICATION = 0
    ENCRYPTION_KEY = 1
    PASSWORD = 2
    USERNAME = 3
    TERMINAL_TYPE = 4
    TERMINAL_WIDTH = 5
    TERMINAL_HEIGHT = 6
    END_AUTHENTICATION = 9


class PacketError(ValueError):
    pass


@dataclass(frozen=True)
class ControlPacket:
    type: ControlType
    data: bytes = b""

    def to_bytes(self) -> bytes:
        return pack(">4sbI", CONTROL_MAGIC, int(self.type), len(self.data)) + self.data

    @classmethod
    def parse_all(cls, blob: bytes) -> list[ControlPacket]:
        packets: list[ControlPacket] = []
        offset = 0
        while offset < len(blob):
            if len(blob) - offset < 9:
                raise PacketError("truncated control packet header")
            magic, raw_type, length = unpack(">4sbI", blob[offset:offset + 9])
            if magic != CONTROL_MAGIC:
                raise PacketError("bad control-packet magic")
            start, end = offset + 9, offset + 9 + length
            if end > len(blob):
                raise PacketError("truncated control-packet data")
            packets.append(cls(ControlType(raw_type), blob[start:end]))
            offset = end
        return packets


@dataclass
class Packet:
    message_type: MessageType
    src_mac: bytes
    dst_mac: bytes
    session_id: int
    byte_counter: int = 0
    version: int = PROTOCOL_VERSION
    client_type: int = CLIENT_TYPE
    data: bytes = b""
    control_packets: list[ControlPacket] = field(default_factory=list)

    @property
    def payload(self) -> bytes:
        if self.control_packets:
            return b"".join(cp.to_bytes() for cp in self.control_packets)
        return self.data

    @property
    def payload_len(self) -> int:
        return len(self.payload)

    def to_bytes(self) -> bytes:
        header = pack(">BB6s6sHHI", self.version, int(self.message_type), self.src_mac,
                      self.dst_mac, self.session_id, self.client_type, self.byte_counter)
        return header + self.payload

    @classmethod
    def from_bytes(cls, raw: bytes) -> Packet:
        if len(raw) < HEADER_LEN:
            raise PacketError("packet shorter than header")
        (version, message_type, src_mac, dst_mac, session_id, client_type,
         byte_counter) = unpack(">BB6s6sHHI", raw[:HEADER_LEN])
        body = raw[HEADER_LEN:]
        packet = cls(
            message_type=MessageType(message_type), src_mac=src_mac, dst_mac=dst_mac,
            session_id=session_id, byte_counter=byte_counter, version=version,
            client_type=client_type,
        )
        if body[:4] == CONTROL_MAGIC:
            packet.control_packets = ControlPacket.parse_all(body)
        else:
            packet.data = body
        return packet


def mac_bytes(mac: str) -> bytes:
    cleaned = mac.replace(":", "").replace("-", "")
    if len(cleaned) != 12:
        raise PacketError(f"invalid MAC address: {mac!r}")
    return bytes.fromhex(cleaned)


def format_mac(raw: bytes) -> str:
    return ":".join(f"{b:02X}" for b in raw)
