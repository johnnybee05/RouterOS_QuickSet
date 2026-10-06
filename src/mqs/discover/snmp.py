"""A single SNMPv2c GET, built by hand.

Three OIDs are wanted and no more: `sysObjectID`, which says MikroTik and nothing else can
be mistaken for it; `sysDescr`; and `sysName`. A library for that would be a dependency
with an async story, a release cadence and a licence, for about a hundred lines of BER —
and this codebase already hand-writes MNDP's TLVs and EC-SRP5 for the same reason.

What makes this worth having at all: SNMP answers where MNDP does not reach and nobody
has credentials. It is a read, it is one packet, and what comes back is `PROBABLE` — the
enterprise OID is a strong signal and still not a login.
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from mqs.discover.candidates import Sighting, Source

SNMP_PORT = 161
DEFAULT_TIMEOUT_S = 2.0
# iso.org.dod.internet.private.enterprises.mikrotik — the one that settles it. A sysObjectID
# under this prefix is a MikroTik; nothing else is allocated it.
MIKROTIK_ENTERPRISE = "1.3.6.1.4.1.14988"
SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
SYS_NAME = "1.3.6.1.2.1.1.5.0"
WANTED = (SYS_DESCR, SYS_OBJECT_ID, SYS_NAME)

SEQUENCE = 0x30
INTEGER = 0x02
OCTET_STRING = 0x04
NULL = 0x05
OID = 0x06
GET_REQUEST = 0xA0
GET_RESPONSE = 0xA2
VERSION_2C = 1
MAX_PACKET = 4096


class SnmpError(ValueError):
    """The answer was not an SNMP response this module understands."""


# ------------------------------------------------------------------ BER, the little of it


def _length(value: int) -> bytes:
    if value < 0x80:
        return bytes([value])
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def _tlv(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + _length(len(body)) + body


def _integer(value: int) -> bytes:
    size = max(1, (value.bit_length() + 8) // 8)
    return _tlv(INTEGER, value.to_bytes(size, "big", signed=True))


def _base128(value: int) -> bytes:
    """One subidentifier, seven bits per byte, high bit set on all but the last."""
    chunk = bytearray([value & 0x7F])
    value >>= 7
    while value:
        chunk.insert(0, (value & 0x7F) | 0x80)
        value >>= 7
    return bytes(chunk)


def encode_oid(text: str) -> bytes:
    """Dotted OID to BER.

    The first two arcs are combined into one subidentifier, `a * 40 + b` — and that
    combination is **not** a single byte: the second arc is unbounded when the first is 2,
    so `2.999` is a legal OID whose first subidentifier is 1079 and takes two bytes like
    any other.
    """
    parts = [int(p) for p in text.split(".")]
    if len(parts) < 2:
        raise SnmpError(f"not an OID: {text!r}")
    body = _base128(parts[0] * 40 + parts[1])
    for arc in parts[2:]:
        body += _base128(arc)
    return _tlv(OID, body)


def decode_oid(body: bytes) -> str:
    """BER back to dotted, splitting the first subidentifier the way it was combined.

    Below 80 the first arc is 0 or 1 and the division recovers both; at 80 and above the
    first arc is 2 and the rest is the second, however large.
    """
    if not body:
        return ""
    values: list[int] = []
    value = 0
    for byte in body:
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            values.append(value)
            value = 0
    if not values:
        return ""
    head = values[0]
    arcs = [2, head - 80] if head >= 80 else [head // 40, head % 40]
    return ".".join(str(a) for a in (*arcs, *values[1:]))


def _read_tlv(data: bytes, offset: int) -> tuple[int, bytes, int]:
    if offset + 2 > len(data):
        raise SnmpError("the packet ends inside a field")
    tag = data[offset]
    size = data[offset + 1]
    offset += 2
    if size & 0x80:
        count = size & 0x7F
        if count == 0 or offset + count > len(data):
            raise SnmpError("a field says it has no length this module can read")
        size = int.from_bytes(data[offset:offset + count], "big")
        offset += count
    if offset + size > len(data):
        raise SnmpError("a field is longer than the packet")
    return tag, data[offset:offset + size], offset + size


def build_get(community: str, oids: Sequence[str], request_id: int = 1) -> bytes:
    """One GET for several OIDs: the whole conversation in a single packet."""
    bindings = b"".join(_tlv(SEQUENCE, encode_oid(oid) + _tlv(NULL, b""))
                        for oid in oids)
    body = (_integer(request_id) + _integer(0) + _integer(0)
            + _tlv(SEQUENCE, bindings))
    message = (_integer(VERSION_2C) + _tlv(OCTET_STRING, community.encode())
               + _tlv(GET_REQUEST, body))
    return _tlv(SEQUENCE, message)


def parse_response(data: bytes) -> dict[str, str]:
    """OID -> value, for the bindings that came back. Raises `SnmpError` on anything else.

    Values are returned as text: an OID dotted, an octet string decoded, everything else
    as an integer. Nothing here needs more than that.
    """
    tag, body, _ = _read_tlv(data, 0)
    if tag != SEQUENCE:
        raise SnmpError("not an SNMP message")
    _, _, offset = _read_tlv(body, 0)  # version
    _, _, offset = _read_tlv(body, offset)  # community
    tag, pdu, _ = _read_tlv(body, offset)
    if tag != GET_RESPONSE:
        raise SnmpError(f"not a response ({tag:#04x})")
    offset = 0
    for _ in range(3):  # request id, error status, error index
        _, _, offset = _read_tlv(pdu, offset)
    tag, bindings, _ = _read_tlv(pdu, offset)
    if tag != SEQUENCE:
        raise SnmpError("the response carries no variable bindings")
    values: dict[str, str] = {}
    offset = 0
    while offset < len(bindings):
        tag, binding, offset = _read_tlv(bindings, offset)
        if tag != SEQUENCE:
            continue
        name_tag, name, inner = _read_tlv(binding, 0)
        if name_tag != OID:
            continue
        value_tag, value, _ = _read_tlv(binding, inner)
        values[decode_oid(name)] = _value(value_tag, value)
    return values


def _value(tag: int, body: bytes) -> str:
    if tag == OID:
        return decode_oid(body)
    if tag == OCTET_STRING:
        return body.decode("utf-8", "replace").strip("\x00").strip()
    if tag == INTEGER:
        return str(int.from_bytes(body, "big", signed=True))
    return ""


# ------------------------------------------------------------------ asking


@dataclass(frozen=True)
class SnmpReply:
    address: str
    sys_object_id: str = ""
    sys_descr: str = ""
    sys_name: str = ""

    @property
    def mikrotik(self) -> bool:
        """Under the MikroTik enterprise OID, or saying so in its description.

        The OID is the reliable half. `sysDescr` is a string a device may be configured to
        say anything in, so it only ever confirms what the OID already said.
        """
        return (self.sys_object_id.startswith(MIKROTIK_ENTERPRISE + ".")
                or self.sys_object_id == MIKROTIK_ENTERPRISE)

    @property
    def version(self) -> str:
        """RouterOS writes `RouterOS RB760iGS` or similar in sysDescr; the version is in
        sysDescr on some releases and not on others, so this stays best-effort."""
        words = self.sys_descr.split()
        return next((w for w in words if w[:1].isdigit() and "." in w), "")


def query(address: str, community: str, *, timeout_s: float = DEFAULT_TIMEOUT_S,
          port: int = SNMP_PORT,
          send: Callable[[bytes, str, int, float], bytes] | None = None) -> SnmpReply | None:
    """One GET to one address. None when nothing answered or the answer made no sense.

    Never raises: a scan asks hundreds of addresses and most of them will not answer.
    """
    transfer = send or _exchange
    try:
        data = transfer(build_get(community, WANTED), address, port, timeout_s)
        values = parse_response(data)
    except (OSError, SnmpError, ValueError):
        return None
    return SnmpReply(address, values.get(SYS_OBJECT_ID, ""), values.get(SYS_DESCR, ""),
                     values.get(SYS_NAME, ""))


def _exchange(packet: bytes, address: str, port: int, timeout_s: float) -> bytes:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout_s)
        sock.sendto(packet, (address, port))
        data, _ = sock.recvfrom(MAX_PACKET)
    return data


def to_sighting(reply: SnmpReply) -> Sighting:
    """What SNMP contributes: an address, a name, and whether it is a MikroTik at all.

    A reply that is not MikroTik still becomes a sighting. "Answers SNMP, is not a
    MikroTik" is a useful row on a network survey, and leaving it out would mean the next
    scan asks the same address again for the same nothing.
    """
    return Sighting(
        source=Source.SNMP,
        address=reply.address,
        identity=reply.sys_name,
        version=reply.version,
        board=reply.sys_descr,
        platform="MikroTik" if reply.mikrotik else (reply.sys_descr or "unknown"),
        note=reply.sys_object_id,
    )
