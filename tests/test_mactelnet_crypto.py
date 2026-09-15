"""EC-SRP5 curve, handshake and packet codec (deterministic, no network)."""

from __future__ import annotations

import hashlib

import pytest

from mqs.transport.mactelnet import curve
from mqs.transport.mactelnet.ecsrp import (
    EcSrpClient,
    EcSrpServer,
    ServerChallenge,
    register_user,
)
from mqs.transport.mactelnet.packet import (
    CLIENT_TYPE,
    CONTROL_MAGIC,
    ControlPacket,
    ControlType,
    MessageType,
    Packet,
    PacketError,
    format_mac,
    mac_bytes,
)

# ---------------------------------------------------------------- curve


def test_generator_on_curve_and_order():
    assert curve.is_on_curve(curve.GENERATOR)
    assert curve.scalar_mult(curve.ORDER, curve.GENERATOR).is_infinity
    assert curve.scalar_mult(0, curve.GENERATOR).is_infinity


def test_point_addition_laws():
    g = curve.GENERATOR
    g2 = curve.add(g, g)
    assert curve.is_on_curve(g2)
    assert curve.scalar_mult(2, g) == g2
    assert curve.add(g, curve.negate(g)).is_infinity
    assert curve.add(g, curve.INFINITY) == g
    # k*G + G == (k+1)*G
    for k in (1, 5, 12345, curve.ORDER - 2):
        assert curve.add(curve.scalar_mult(k, g), g) == curve.scalar_mult(k + 1, g)


def test_lift_x_to_montgomery_roundtrip():
    for scalar in (1, 2, 7, 99, 2**200 + 3):
        mont_x, parity = curve.generator_multiple(scalar)
        point = curve.lift_x(int.from_bytes(mont_x, "big"), parity)
        assert point is not None and curve.is_on_curve(point)
        assert curve.to_montgomery(point) == (mont_x, parity)


def test_lift_x_rejects_non_residue():
    # Find an x with no corresponding curve point and confirm lift_x returns None.
    found_none = False
    for candidate in range(2, 200):
        y_sq = (candidate**3 + curve.MONT_A * candidate * candidate + candidate) % curve.P
        if not curve.mod_sqrt(y_sq):
            assert curve.lift_x(candidate, 0) is None
            found_none = True
            break
    assert found_none


def test_redp1_is_deterministic_and_on_curve():
    p1 = curve.redp1(b"some-x-value", 1)
    p2 = curve.redp1(b"some-x-value", 1)
    assert p1 == p2 and curve.is_on_curve(p1)
    assert p1.y & 1 == 1
    assert curve.redp1(b"other", 1) != p1


def test_password_validator():
    salt = bytes(range(16))
    expected = hashlib.sha256(salt + hashlib.sha256(b"admin:secret").digest()).digest()
    assert curve.password_validator("admin", "secret", salt) == expected
    with pytest.raises(ValueError):
        curve.password_validator("a", "b", b"short")


# ---------------------------------------------------------------- handshake


def test_full_handshake_matches_on_correct_password():
    user = register_user("admin", "s3cret", salt=bytes(range(16)))
    client = EcSrpClient("admin", "s3cret", private=bytes([1] + [0] * 31))
    server = EcSrpServer(user, private=bytes([2] + [0] * 31))
    challenge = server.challenge()
    assert len(challenge.public) == 32 and len(challenge.salt) == 16
    client_cc = client.confirmation_code(challenge)
    server_cc = server.expected_confirmation(client.hello())
    assert client_cc == server_cc
    assert len(client_cc) == 32


def test_handshake_is_randomised_but_consistent():
    user = register_user("admin", "pw")
    for _ in range(3):
        client = EcSrpClient("admin", "pw")
        server = EcSrpServer(user)
        challenge = server.challenge()
        assert client.confirmation_code(challenge) == server.expected_confirmation(client.hello())


def test_wrong_password_fails():
    user = register_user("admin", "correct-horse")
    client = EcSrpClient("admin", "wrong-horse")
    server = EcSrpServer(user)
    challenge = server.challenge()
    assert client.confirmation_code(challenge) != server.expected_confirmation(client.hello())


def test_wrong_username_fails():
    user = register_user("admin", "pw")
    client = EcSrpClient("attacker", "pw")
    server = EcSrpServer(user)
    assert (client.confirmation_code(server.challenge())
            != server.expected_confirmation(client.hello()))


def test_confirmation_validates_salt_length():
    client = EcSrpClient("admin", "pw")
    with pytest.raises(ValueError):
        client.confirmation_code(ServerChallenge(b"\x00" * 32, 0, b"short"))


def test_client_hello_shape():
    client = EcSrpClient("admin", "pw", private=bytes([9] + [0] * 31))
    hello = client.hello()
    assert hello.username == "admin"
    assert len(hello.public) == 32 and hello.parity in (0, 1)
    # Deterministic for a fixed private key.
    assert EcSrpClient("admin", "pw", private=bytes([9] + [0] * 31)).public == hello.public


# ---------------------------------------------------------------- packets


def test_control_packet_roundtrip():
    cp = ControlPacket(ControlType.ENCRYPTION_KEY, b"payload")
    raw = cp.to_bytes()
    assert raw[:4] == CONTROL_MAGIC
    assert ControlPacket.parse_all(raw) == [cp]
    empty = ControlPacket(ControlType.BEGIN_AUTHENTICATION)
    assert ControlPacket.parse_all(empty.to_bytes()) == [empty]


def test_multiple_control_packets():
    packets = [ControlPacket(ControlType.PASSWORD, b"\x01\x02"),
               ControlPacket(ControlType.USERNAME, b"admin"),
               ControlPacket(ControlType.TERMINAL_WIDTH, (80).to_bytes(2, "little"))]
    blob = b"".join(p.to_bytes() for p in packets)
    assert ControlPacket.parse_all(blob) == packets


@pytest.mark.parametrize("blob", [b"\x00" * 5, CONTROL_MAGIC + b"\x01\x00\x00\x00\x05ab",
                                  b"\xde\xad\xbe\xef\x00\x00\x00\x00\x00"])
def test_control_packet_parse_errors(blob):
    with pytest.raises(PacketError):
        ControlPacket.parse_all(blob)


def test_packet_with_control_packets_roundtrip():
    packet = Packet(MessageType.DATA, mac_bytes("48:8f:5a:01:02:03"),
                    mac_bytes("dc:2c:6e:aa:bb:cc"), session_id=0x1234, byte_counter=7)
    packet.control_packets = [ControlPacket(ControlType.BEGIN_AUTHENTICATION),
                              ControlPacket(ControlType.ENCRYPTION_KEY, b"\xaa" * 33)]
    restored = Packet.from_bytes(packet.to_bytes())
    assert restored.message_type == MessageType.DATA
    assert restored.session_id == 0x1234 and restored.byte_counter == 7
    assert restored.client_type == CLIENT_TYPE
    assert restored.src_mac == packet.src_mac and restored.dst_mac == packet.dst_mac
    assert restored.control_packets == packet.control_packets
    assert restored.payload_len == packet.payload_len


def test_packet_with_terminal_data_roundtrip():
    packet = Packet(MessageType.DATA, b"\x00" * 6, b"\xff" * 6, session_id=1, data=b"login\r")
    restored = Packet.from_bytes(packet.to_bytes())
    assert restored.control_packets == [] and restored.data == b"login\r"
    assert restored.payload_len == 6


def test_packet_header_sizes():
    packet = Packet(MessageType.START_SESSION, b"\x00" * 6, b"\x11" * 6, session_id=0)
    assert len(packet.to_bytes()) == 0x16  # empty START_SESSION is exactly the header
    with pytest.raises(PacketError):
        Packet.from_bytes(b"\x00" * 5)


def test_mac_helpers():
    assert mac_bytes("48:8F:5A:01:02:03") == bytes.fromhex("488f5a010203")
    assert mac_bytes("48-8f-5a-01-02-03") == bytes.fromhex("488f5a010203")
    assert format_mac(bytes.fromhex("488f5a010203")) == "48:8F:5A:01:02:03"
    with pytest.raises(PacketError):
        mac_bytes("00:11:22")
