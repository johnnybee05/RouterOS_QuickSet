"""EC-SRP5 handshake used by RouterOS MAC-Telnet and Winbox (6.43+).

Message flow (client side is what we need; the server side is here only so the handshake
can be exercised end to end in tests):

  client -> server:  username, client public x (Wc, Montgomery), parity of Wc.y
  server -> client:  server public x (Ws, Montgomery), parity of Ws.y, 16-byte salt
  client:            derives the shared point and sends confirmation Cc = SHA256(h | z.x)

The exact arithmetic follows MarginResearch/mikrotik_authentication (Apache-2.0); see
THIRD_PARTY_NOTICES.md. Not verified against a real device (NUTNO OVĚŘIT).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from mqs.transport.mactelnet import curve
from mqs.transport.mactelnet.curve import Point

SALT_LEN = 16


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


@dataclass(frozen=True)
class ClientHello:
    username: str
    public: bytes  # 32-byte Montgomery x of Wc
    parity: int


@dataclass(frozen=True)
class ServerChallenge:
    public: bytes  # 32-byte Montgomery x of Ws
    parity: int
    salt: bytes


class EcSrpClient:
    """Client half of the handshake. One instance per connection attempt."""

    def __init__(self, username: str, password: str, private: bytes | None = None) -> None:
        self.username = username
        self.password = password
        self.private = private or secrets.token_bytes(32)
        self.public, self.parity = curve.generator_multiple(self.private)

    def hello(self) -> ClientHello:
        return ClientHello(self.username, self.public, self.parity)

    def confirmation_code(self, challenge: ServerChallenge) -> bytes:
        if len(challenge.salt) != SALT_LEN:
            raise ValueError("server salt must be 16 bytes")
        validator = curve.password_validator(self.username, self.password, challenge.salt)
        validator_x, _ = curve.generator_multiple(validator)
        e = curve.redp1(validator_x, 1)
        server_point = curve.lift_x(int.from_bytes(challenge.public, "big"), challenge.parity)
        if server_point is None:
            raise ValueError("server public value is not a valid point")
        # The client adds e back to recover the server's ephemeral Ts*G.
        shared_base = curve.add(server_point, e)

        h = _sha256(self.public + challenge.public)
        vh = int.from_bytes(validator, "big") * int.from_bytes(h, "big")
        vh = (vh + int.from_bytes(self.private, "big")) % curve.ORDER
        z = curve.scalar_mult(vh, shared_base)
        z_x, _ = curve.to_montgomery(z)
        return _sha256(h + z_x)


@dataclass(frozen=True)
class RegisteredUser:
    """What a server stores per user. Real devices keep only the verifier x; the parity is
    kept here so the test server can reconstruct the exact verifier point V = i*G."""

    salt: bytes
    validator_x: bytes
    validator_parity: int


def register_user(username: str, password: str, salt: bytes | None = None) -> RegisteredUser:
    salt = salt or secrets.token_bytes(SALT_LEN)
    validator = curve.password_validator(username, password, salt)
    validator_x, parity = curve.generator_multiple(validator)
    return RegisteredUser(salt=salt, validator_x=validator_x, validator_parity=parity)


class EcSrpServer:
    """Server half — used only by tests and a local simulator, never against real devices."""

    def __init__(self, user: RegisteredUser, private: bytes | None = None) -> None:
        self.user = user
        self.private = private or secrets.token_bytes(32)
        self._base = curve.scalar_mult(int.from_bytes(self.private, "big"), curve.GENERATOR)

    def challenge(self) -> ServerChallenge:
        e = curve.redp1(self.user.validator_x, 1)
        sent_point = curve.add(self._base, curve.negate(e))  # (Ts*G) - e
        public, parity = curve.to_montgomery(sent_point)
        return ServerChallenge(public, parity, self.user.salt)

    def verifier_point(self) -> Point:
        point = curve.lift_x(int.from_bytes(self.user.validator_x, "big"),
                             self.user.validator_parity)
        assert point is not None
        return point

    def expected_confirmation(self, hello: ClientHello) -> bytes:
        """The Cc the server derives; equals the client's value on a correct password."""
        client_point = curve.lift_x(int.from_bytes(hello.public, "big"), hello.parity)
        if client_point is None:
            raise ValueError("client public value is not a valid point")
        server_public, _ = curve.to_montgomery(curve.add(self._base,
                                                         curve.negate(curve.redp1(
                                                             self.user.validator_x, 1))))
        h = _sha256(hello.public + server_public)
        combined = curve.add(client_point,
                             curve.scalar_mult(int.from_bytes(h, "big"), self.verifier_point()))
        z = curve.scalar_mult(int.from_bytes(self.private, "big"), combined)
        z_x, _ = curve.to_montgomery(z)
        return _sha256(h + z_x)
