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

# RouterOS writes this into the terminal stream when it refuses a login.
LOGIN_FAILED = "login failed"
# Tail of the interactive console prompt, e.g. "[bee@QuickSet] > ".
PROMPT = "] > "


def _strip_console(text: str, command: str) -> str:
    """Drop the echoed command line and the trailing prompt from console output."""
    lines = text.splitlines()
    wanted = command.strip()
    if wanted and lines and lines[0].strip().endswith(wanted):
        lines = lines[1:]
    # sanitize() trims the prompt's trailing space, so match on the bracket-arrow tail.
    while lines and (not lines[-1].strip() or lines[-1].rstrip().endswith(">")):
        lines.pop()
    return "\n".join(lines).strip()


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
    """Real MAC-Telnet channel: broadcasts to :20561 from an ephemeral source port.

    Two things are load-bearing here, both verified on RouterOS 7.20.7:

    * The source port must NOT be 20561. The device answers to whatever port the request
      came from (its reply arrives from source IP 0.0.0.0), so a socket bound to 20561
      never sees it.
    * The socket must bind INADDR_ANY. The reply is a broadcast, and Windows does not
      deliver broadcast datagrams to a socket bound to a specific unicast address.
      Interface selection therefore comes from `broadcast` (the subnet's directed
      broadcast), not from the bind address.
    """

    def __init__(self, local_address: str = "0.0.0.0", port: int = MACTELNET_PORT,
                 broadcast: str = "255.255.255.255") -> None:
        self.port = port
        # Directed broadcast (192.168.55.255) picks the interface via the routing table;
        # binding to the NIC address instead would lose the reply, see the docstring.
        self.broadcast = broadcast if local_address != "0.0.0.0" else "255.255.255.255"
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self._sock.bind(("", 0))
        except OSError:
            self._sock.close()
            raise

    def send(self, data: bytes) -> None:
        self._sock.sendto(data, (self.broadcast, self.port))

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
        self._primed = False

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
            if packet.src_mac == self.src_mac:
                continue  # our own broadcast echoed back
            # RouterOS answers with the two 16-bit header fields in the opposite order
            # from the request: verified on 7.20.7, a request carrying session 0xBEEF in
            # the first field is answered with 0x0015 there and 0xBEEF in the second.
            if self.session_id not in (packet.session_id, packet.client_type):
                continue  # not our session
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
        self._check_login(deadline)

    def _check_login(self, deadline: float, window: float = 1.5) -> None:
        """A rejected login arrives as terminal text, not as a control packet.

        Verified on RouterOS 7.20.7: the handshake finishes normally, END_AUTHENTICATION
        and all, and only then does the device write "Login failed, incorrect username or
        password" into the terminal stream. Without this check authenticate() reports
        success for a login the device refused.
        """
        end = min(deadline, self._clock() + window)
        text = ""
        while self._clock() < end:
            packet = self._recv(end)
            if packet is None:
                break
            if packet.message_type != MessageType.DATA:
                continue
            self._send_ack(packet)
            if packet.control_packets:
                continue
            text += packet.data.decode("utf-8", errors="replace")
            if LOGIN_FAILED in text.lower():
                self._authenticated = False
                raise MacTelnetAuthError(text.strip().splitlines()[-1].strip())

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

    def _send_line(self, line: str) -> None:
        data = self._packet(MessageType.DATA)
        data.data = (line + "\r").encode()
        self._send(data)

    def _collect(self, timeout: float, quiet: float) -> str:
        """Read terminal output until the prompt comes back, or the stream goes quiet."""
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
                    # Stop at a prompt that ENDS the output. Testing for "contains"
                    # would stop on the echoed command, which the console prefixes
                    # with the previous prompt, cutting the real output off.
                    acc = b"".join(collected).decode("utf-8", errors="replace")
                    if acc.rstrip(" \t\r\n").endswith(">"):
                        break
            elif packet.message_type == MessageType.END_SESSION:
                break
        return b"".join(collected).decode("utf-8", errors="replace")

    def run(self, line: str, *, quiet: float = 0.4, timeout: float = 10.0) -> str:
        """Send one CLI line and return its output, without echo or prompt."""
        if not self._authenticated:
            raise MacTelnetError("not authenticated")
        if not self._primed:
            # Verified on RouterOS 7.20.7: the console stays silent until it gets its
            # first input, then floods the banner, the unread log backlog and finally
            # the prompt. Anything sent before that is swallowed by the banner.
            self._send_line("")
            self._collect(timeout, quiet)
            self._primed = True

        self._send_line(line)
        text = sanitize(self._collect(timeout, quiet))
        error = find_error(text)
        if error is not None:
            raise MacTelnetError(f"{line}: {error}")
        return _strip_console(text, line)

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
