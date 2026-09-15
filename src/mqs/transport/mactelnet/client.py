"""Synchronous MAC-Telnet session: EC-SRP5 login then a small command runner.

Only enough of the protocol for the connect-by-MAC bootstrap (spec 4.x / addendum §10): log
in and run a couple of CLI lines to add a temporary management IP. The datagram channel is
injected, so the whole session is exercised in tests against an in-process fake device.
Not verified against a real device (docs/DIALECT_NOTES.md, NUTNO OVĚŘIT).
"""

from __future__ import annotations

import secrets
import socket
import time
import uuid
from collections.abc import Callable
from typing import Protocol

from mqs.transport.base import TransportError
from mqs.transport.cli_output import find_error, sanitize
from mqs.transport.mactelnet.ecsrp import EcSrpClient, ServerChallenge
from mqs.transport.mactelnet.packet import (
    MACTELNET_PORT,
    ControlPacket,
    ControlType,
    MessageType,
    Packet,
    PacketError,
    mac_bytes,
)


class MacTelnetError(TransportError):
    pass


class MacTelnetAuthError(MacTelnetError):
    pass


class DatagramChannel(Protocol):
    """Broadcast UDP transport for MAC-Telnet (bound to :20561)."""

    def send(self, data: bytes) -> None: ...

    def receive(self, timeout: float) -> bytes | None: ...

    def close(self) -> None: ...


def _counter(value: int) -> int:
    return value % 65536


class UdpBroadcastChannel:
    """Real MAC-Telnet channel: a UDP socket bound to :20561 sending L2 broadcasts.

    `local_address` binds to one NIC so the broadcast leaves the right interface. Windows
    Firewall may block inbound UDP 20561; that surfaces as a receive timeout.
    """

    def __init__(self, local_address: str = "0.0.0.0", port: int = MACTELNET_PORT) -> None:
        self.port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self._sock.bind((local_address, port))
        except OSError:
            self._sock.close()
            raise

    def send(self, data: bytes) -> None:
        self._sock.sendto(data, ("255.255.255.255", self.port))

    def receive(self, timeout: float) -> bytes | None:
        self._sock.settimeout(max(0.0, timeout))
        try:
            data, _ = self._sock.recvfrom(4096)
        except (TimeoutError, BlockingIOError):
            return None
        except OSError:
            return None
        return data

    def close(self) -> None:
        self._sock.close()


class MacTelnetSession:
    def __init__(
        self,
        channel: DatagramChannel,
        dst_mac: str,
        username: str,
        password: str,
        *,
        src_mac: bytes | None = None,
        term_type: bytes = b"vt100",
        term_size: tuple[int, int] = (80, 24),
        connect_timeout: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.channel = channel
        self.dst_mac = mac_bytes(dst_mac)
        self.src_mac = src_mac or uuid.getnode().to_bytes(6, "big")
        self.username = username
        self.password = password
        self.term_type = term_type
        self.term_size = term_size
        self.connect_timeout = connect_timeout
        self._clock = clock
        self.session_id = secrets.randbits(16)
        self.send_counter = 0
        self.receive_counter = 0
        self._srp = EcSrpClient(username, password)
        self._authenticated = False

    # ------------------------------------------------------------ low level

    def _packet(self, message_type: MessageType) -> Packet:
        return Packet(message_type, self.src_mac, self.dst_mac, self.session_id,
                      byte_counter=self.send_counter)

    def _send(self, packet: Packet) -> None:
        self.channel.send(packet.to_bytes())
        self.send_counter = _counter(self.send_counter + packet.payload_len)

    def _send_ack(self, packet: Packet) -> None:
        ack = self._packet(MessageType.ACKNOWLEDGE)
        ack.byte_counter = _counter(packet.byte_counter + packet.payload_len)
        self.channel.send(ack.to_bytes())

    def _recv(self, deadline: float) -> Packet | None:
        while self._clock() < deadline:
            raw = self.channel.receive(max(0.0, deadline - self._clock()))
            if raw is None:
                continue
            try:
                packet = Packet.from_bytes(raw)
            except PacketError:
                continue
            if packet.session_id != self.session_id or packet.src_mac == self.src_mac:
                continue  # not our session, or our own broadcast echoed back
            return packet
        return None

    def _wait_for(self, deadline: float, *types: MessageType) -> Packet:
        while True:
            packet = self._recv(deadline)
            if packet is None:
                raise MacTelnetError("timed out waiting for the device")
            if packet.message_type == MessageType.DATA:
                self._send_ack(packet)
            if packet.message_type in types:
                return packet

    # ------------------------------------------------------------ handshake

    def authenticate(self) -> None:
        deadline = self._clock() + self.connect_timeout
        self._send(self._packet(MessageType.START_SESSION))
        self._wait_for(deadline, MessageType.ACKNOWLEDGE)

        hello = self._srp.hello()
        key_data = hello.username.encode() + b"\x00" + hello.public + bytes([hello.parity])
        begin = self._packet(MessageType.DATA)
        begin.control_packets = [ControlPacket(ControlType.BEGIN_AUTHENTICATION),
                                 ControlPacket(ControlType.ENCRYPTION_KEY, key_data)]
        self._send(begin)

        challenge_packet = self._wait_for(deadline, MessageType.DATA)
        challenge = self._read_challenge(challenge_packet)
        confirmation = self._srp.confirmation_code(challenge)

        reply = self._packet(MessageType.DATA)
        width = self.term_size[0].to_bytes(2, "little")
        height = self.term_size[1].to_bytes(2, "little")
        reply.control_packets = [
            ControlPacket(ControlType.PASSWORD, confirmation),
            ControlPacket(ControlType.USERNAME, self.username.encode()),
            ControlPacket(ControlType.TERMINAL_TYPE, self.term_type),
            ControlPacket(ControlType.TERMINAL_WIDTH, width),
            ControlPacket(ControlType.TERMINAL_HEIGHT, height),
        ]
        self._send(reply)

        end = self._wait_for(deadline, MessageType.DATA)
        if not any(cp.type == ControlType.END_AUTHENTICATION for cp in end.control_packets):
            raise MacTelnetAuthError("device did not end the authentication phase")
        self._authenticated = True

    def _read_challenge(self, packet: Packet) -> ServerChallenge:
        control = next((cp for cp in packet.control_packets
                        if cp.type == ControlType.ENCRYPTION_KEY), None)
        if control is None or len(control.data) < 0x21:
            raise MacTelnetAuthError("device did not send an encryption key")
        salt = control.data[0x21:]
        if len(salt) != 16:
            raise MacTelnetAuthError("user is not registered on the device")
        return ServerChallenge(control.data[:0x20], control.data[0x20], salt)

    # ------------------------------------------------------------ commands

    def run(self, line: str, *, quiet: float = 0.4, timeout: float = 10.0) -> str:
        """Send one CLI line and collect terminal output until it goes quiet."""
        if not self._authenticated:
            raise MacTelnetError("not authenticated")
        data = self._packet(MessageType.DATA)
        data.data = (line + "\r").encode()
        self._send(data)

        collected: list[bytes] = []
        overall = self._clock() + timeout
        last = self._clock()
        while self._clock() < overall:
            packet = self._recv(min(overall, last + quiet))
            if packet is None:
                if self._clock() >= last + quiet:
                    break
                continue
            if packet.message_type == MessageType.DATA:
                self._send_ack(packet)
                if not packet.control_packets:
                    collected.append(packet.data)
                    last = self._clock()
            elif packet.message_type == MessageType.END_SESSION:
                break
        text = sanitize(b"".join(collected).decode("utf-8", errors="replace"))
        error = find_error(text)
        if error is not None:
            raise MacTelnetError(f"{line}: {error}")
        return text

    def close(self) -> None:
        try:
            self._send(self._packet(MessageType.END_SESSION))
        except (OSError, TransportError):
            pass
        finally:
            self.channel.close()

    def __enter__(self) -> MacTelnetSession:
        self.authenticate()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
