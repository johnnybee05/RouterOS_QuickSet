"""In-memory MAC-Telnet device speaking the real packet + EC-SRP5 protocol, for tests."""

from __future__ import annotations

from collections.abc import Callable

from mqs.transport.mactelnet.ecsrp import (
    ClientHello,
    EcSrpServer,
    RegisteredUser,
    register_user,
)
from mqs.transport.mactelnet.packet import (
    ControlPacket,
    ControlType,
    MessageType,
    Packet,
    mac_bytes,
)


class FakeDevice:
    """Answers a MacTelnetSession over a paired FakeChannel; runs canned command replies."""

    def __init__(
        self,
        mac: str = "dc:2c:6e:00:11:22",
        username: str = "admin",
        password: str = "",
        *,
        user: RegisteredUser | None = None,
        command_reply: Callable[[str], str] = lambda line: line + "\n[admin@MikroTik] > ",
        server_private: bytes | None = None,
    ) -> None:
        self.mac = mac_bytes(mac)
        self.user = user or register_user(username, password)
        self.server = EcSrpServer(self.user, private=server_private)
        self.command_reply = command_reply
        self.commands: list[str] = []
        self.session_id: int | None = None
        self.client_mac: bytes | None = None
        self.send_counter = 0
        self.authenticated = False
        self.expected_cc: bytes | None = None
        self.reject_confirmation = False

    def _packet(self, message_type: MessageType) -> Packet:
        return Packet(message_type, self.mac, self.client_mac or b"\x00" * 6,
                      self.session_id or 0, byte_counter=self.send_counter)

    def _emit(self, packet: Packet, out: list[bytes]) -> None:
        out.append(packet.to_bytes())
        self.send_counter = (self.send_counter + packet.payload_len) % 65536

    def handle(self, raw: bytes) -> list[bytes]:
        packet = Packet.from_bytes(raw)
        self.session_id = packet.session_id
        self.client_mac = packet.src_mac
        out: list[bytes] = []
        if packet.message_type == MessageType.START_SESSION:
            out.append(self._ack(packet))
        elif packet.message_type == MessageType.DATA:
            out.append(self._ack(packet))
            out.extend(self._handle_data(packet))
        elif packet.message_type == MessageType.END_SESSION:
            pass
        return out

    def _ack(self, packet: Packet) -> bytes:
        ack = Packet(MessageType.ACKNOWLEDGE, self.mac, packet.src_mac, packet.session_id,
                     byte_counter=(packet.byte_counter + packet.payload_len) % 65536)
        return ack.to_bytes()

    def _handle_data(self, packet: Packet) -> list[bytes]:
        out: list[bytes] = []
        if not packet.control_packets:
            line = packet.data.decode("utf-8", errors="replace").rstrip("\r\n")
            self.commands.append(line)
            reply = self._packet(MessageType.DATA)
            reply.data = self.command_reply(line).encode()
            self._emit(reply, out)
            return out

        types = {cp.type: cp for cp in packet.control_packets}
        if ControlType.ENCRYPTION_KEY in types and ControlType.BEGIN_AUTHENTICATION in types:
            key = types[ControlType.ENCRYPTION_KEY].data
            username, _, rest = key.partition(b"\x00")
            hello = ClientHello(username.decode(), rest[:32], rest[32])
            self.expected_cc = self.server.expected_confirmation(hello)
            challenge = self.server.challenge()
            reply = self._packet(MessageType.DATA)
            reply.control_packets = [ControlPacket(
                ControlType.ENCRYPTION_KEY,
                challenge.public + bytes([challenge.parity]) + challenge.salt)]
            self._emit(reply, out)
        elif ControlType.PASSWORD in types:
            cc = types[ControlType.PASSWORD].data
            if self.reject_confirmation or cc != self.expected_cc:
                # A real device signals auth failure as terminal data, not a control packet.
                reply = self._packet(MessageType.DATA)
                reply.data = b"login failure for user admin\r\n"
                self._emit(reply, out)
            else:
                self.authenticated = True
                reply = self._packet(MessageType.DATA)
                reply.control_packets = [ControlPacket(ControlType.END_AUTHENTICATION)]
                self._emit(reply, out)
        return out


class FakeChannel:
    """Client-side channel wired to a FakeDevice, with an optional own-broadcast echo."""

    def __init__(self, device: FakeDevice, *, echo_self: bool = False,
                 drop_first_ack: bool = False) -> None:
        self.device = device
        self.echo_self = echo_self
        self.drop_first_ack = drop_first_ack
        self.inbox: list[bytes] = []
        self.sent: list[bytes] = []
        self.closed = False

    def send(self, data: bytes) -> None:
        self.sent.append(data)
        if self.echo_self:
            self.inbox.append(data)  # our own broadcast comes back first
        for reply in self.device.handle(data):
            if self.drop_first_ack:
                self.drop_first_ack = False
                continue
            self.inbox.append(reply)

    def receive(self, timeout: float) -> bytes | None:
        return self.inbox.pop(0) if self.inbox else None

    def close(self) -> None:
        self.closed = True
