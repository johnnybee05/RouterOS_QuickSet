"""Synchronous MAC-Telnet session: EC-SRP5 login then a small command runner.

Only enough of the protocol for connect-by-MAC (spec 4.x / addendum §10): log in and run
CLI lines - the bootstrap address, and file transfer (files.py). The datagram channel is
injected, so the whole session is exercised in tests against an in-process fake device.
Verified against RouterOS 7.20.7; the findings are in docs/DIALECT_NOTES.md.
"""

from __future__ import annotations

import re
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
# RouterOS 7.13+ asks this on the first console login after a reset and waits for an
# answer instead of showing a prompt, which stalls everything sent afterwards.
LICENSE_QUESTION = "software license"
# A user without a password is asked for a new one on every console login: "Change your
# password", then "new password> ". It ends in ">" like the prompt, and whatever is typed
# next becomes the new password. Verified on 7.20.7 (2026-09-21): Ctrl-C skips it, keeps
# the password as it was, and the console carries on to its prompt.
PASSWORD_QUESTION = "password>"
CTRL_C = b"\x03"


# One drawing of the prompt, "[admin@MikroTik] >" (the identity may hold spaces).
_PROMPT_RE = re.compile(r"\[[^@\r\n]+@[^\r\n]*\] >")
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _at_prompt(text: str) -> bool:
    """The console shows its prompt, with nothing typed after it: the command is done.

    The last line has to be the prompt and nothing else, drawn once or more. RouterOS
    6.49.10 redraws the whole line for every character it echoes
    ("\\r\\r[admin@MikroTik] > :pu<spaces>\\x1b[K\\r[admin@MikroTik] > :pu"), so the stream
    passes "[admin@MikroTik] >" many times while the command is still being echoed; a
    packet that happened to end there stopped the read before any output (2026-09-25).
    """
    line = _ANSI_RE.sub("", text).rsplit("\n", 1)[-1]
    drawn = [part.strip() for part in line.split("\r") if part.strip()]
    return bool(drawn) and all(_PROMPT_RE.fullmatch(part) for part in drawn)


def _password_question(text: str) -> bool:
    """The last line of ``text`` asks for a password; it is not the console prompt.

    A prompt always carries "[user@identity]", a question never does. Callers decide what
    "last line" means: with trailing newlines kept, a question the console has already
    moved on from (it repeats it after Ctrl-C) no longer counts as asking.
    """
    line = text.replace("\r", "\n").rsplit("\n", 1)[-1].strip()
    return line.lower().endswith(PASSWORD_QUESTION) and "]" not in line


def _squash(text: str) -> str:
    """Whitespace-free form, for matching an echo the console may have wrapped.

    A command longer than the terminal width comes back broken by carriage returns and
    padding, so the echo never matches the command literally.
    """
    return "".join(text.split())


# How much of a command's end is looked for in its echo.
ECHO_TAIL = 24


def _echo_mark(command: str) -> str:
    """The part of a command its echo is guaranteed to show.

    Verified on 7.20.7: the console stays 80 columns wide whatever width the client asks
    for, and a longer line is scrolled sideways rather than wrapped - the echo shows
    "...chunk-siz>" and then redraws as "< ...=10"]]". The whole line never appears, but
    its end always does, because that is where the cursor is.
    """
    return _squash(command)[-ECHO_TAIL:]


def _strip_console(text: str, command: str) -> str:
    """Drop the echoed command line and the trailing prompt from console output."""
    lines = text.splitlines()
    wanted = _echo_mark(command)
    # Drop everything up to and including the echoed command. splitlines() also splits on
    # the bare CRs the console uses to redraw, and a command wider than the terminal comes
    # back split across lines or scrolled, so match its end on the whitespace-free form as
    # it accumulates.
    if wanted:
        seen = ""
        for index, line in enumerate(lines):
            seen += _squash(line)
            if wanted in seen:
                lines = lines[index + 1:]
                break
        # The console may redraw the finished line more than once, each on a line of its
        # own (verified on 7.20.7: "[admin@MikroTik] > :put [...]" came back twice).
        while lines and _squash(lines[0]).endswith(wanted):
            lines.pop(0)
    # sanitize() trims the prompt's trailing space, so match on the bracket-arrow tail.
    while lines and (not lines[-1].strip() or lines[-1].rstrip().endswith(">")):
        lines.pop()
    return "\n".join(lines).strip()


def _tail(chunks: list[bytes], size: int) -> bytes:
    """The last ``size`` bytes of ``chunks``, without joining all of them."""
    picked: list[bytes] = []
    total = 0
    for chunk in reversed(chunks):
        picked.append(chunk)
        total += len(chunk)
        if total >= size:
            break
    return b"".join(reversed(picked))[-size:]


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
    # The byte counter is the 4-byte field of the header, not 2: real traffic carries
    # values like 0x0004E8B7. Wrapping at 2**16 desynchronised every ACK past 64 KiB,
    # which is less than one RouterOS login banner plus log backlog.
    return value % 2**32


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
        # Asked for; 7.20.7 keeps an 80-column console regardless (see _echo_mark).
        term_size: tuple[int, int] = (200, 24),
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
        self.receive_counter: int | None = None  # counter the next new DATA packet carries
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
            if packet.message_type == MessageType.DATA and not self._is_new(packet):
                self._send_ack(packet)  # its first ACK went missing; confirm it again
                continue
            return packet
        return None

    def _is_new(self, packet: Packet) -> bool:
        """False for DATA the device already sent once and is sending again.

        The device resends a packet whose ACK it has not seen in time. Taking the copy
        as new output put about 250 bytes into a download twice (seen on 7.20.7 while
        reading a 5 KiB file), so, as the reference client does, a packet only counts as
        new once its byte counter has reached the position the stream is at.
        """
        expected = self.receive_counter
        if expected is not None and (packet.byte_counter - expected) % 2**32 >= 2**31:
            return False
        self.receive_counter = _counter(packet.byte_counter + packet.payload_len)
        return True

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

        # The device may interleave terminal data before END_AUTHENTICATION, so read on
        # instead of judging the first DATA packet: that made authenticate() fail at
        # random against a real device.
        while True:
            try:
                packet = self._wait_for(deadline, MessageType.DATA)
            except MacTelnetError as exc:
                raise MacTelnetAuthError(
                    "device did not end the authentication phase") from exc
            if any(cp.type == ControlType.END_AUTHENTICATION for cp in packet.control_packets):
                break
            text = packet.data.decode("utf-8", errors="replace")
            if LOGIN_FAILED in text.lower():
                raise MacTelnetAuthError(text.strip().splitlines()[-1].strip())
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
            if _password_question(text.rstrip(" ")):
                self._skip_password_change()  # not primed: the prompt is still to come
                return
            if text.rstrip(" \t\r\n").endswith(">"):
                # The console already got as far as its prompt here, so run() must not
                # wait for a banner that has been and gone.
                self._primed = True
                return

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

    def _collect(self, timeout: float, quiet: float, expect: str = "") -> tuple[str, bool]:
        """Read terminal output; returns the text and whether a prompt ended it.

        The flag matters: a command that finished always leaves a prompt behind, so text
        without one means we stopped reading early and cannot say the command ran.
        """
        saw_prompt = False
        collected: list[bytes] = []
        mark = _echo_mark(expect) if expect else ""
        echoed = not mark
        overall = self._clock() + timeout
        last: float | None = None
        while self._clock() < overall:
            # Wait the whole timeout for the first byte: after a login the console needs
            # about 2.5 s before it starts sending its banner (verified on 7.20.7), and a
            # quiet window that short would give up long before it speaks. Once it is
            # talking, a quiet gap does mean the output is finished.
            packet = self._recv(overall if last is None else min(overall, last + quiet))
            if packet is None:
                if last is None or self._clock() >= last + quiet:
                    break
                continue
            if packet.message_type == MessageType.DATA:
                self._send_ack(packet)
                if not packet.control_packets:
                    collected.append(packet.data)
                    last = self._clock()
                    # Wait until the console has echoed our command: a prompt left over
                    # from the banner would otherwise end the read straight away and be
                    # returned as the command's output. Looked for only until it is
                    # found, and the prompt only in the tail: re-reading a long download
                    # whole at every packet delays the ACKs the device is waiting for.
                    if not echoed:
                        acc = b"".join(collected).decode("utf-8", errors="replace")
                        echoed = mark in _squash(acc)
                    # Stop at a prompt that ENDS the output. Testing for "contains"
                    # would stop on the echoed command, which the console prefixes
                    # with the previous prompt, cutting the real output off.
                    recent = _tail(collected, 256).decode("utf-8", errors="replace")
                    if _password_question(recent.rstrip(" ")):
                        break  # never let a command be typed in as the new password
                    tail = recent.rstrip(" \t\r\n")
                    if echoed and _at_prompt(recent):
                        saw_prompt = True
                        break
                    if tail.endswith(":") and LICENSE_QUESTION in recent.lower():
                        break  # a question is waiting for an answer, not for more reading
            elif packet.message_type == MessageType.END_SESSION:
                break
        return b"".join(collected).decode("utf-8", errors="replace"), saw_prompt

    def _prime(self, timeout: float) -> None:
        """Wake the console and swallow everything it says before it is ready.

        Verified on RouterOS 7.20.7: after a login the console stays silent until it gets
        input, then takes about 2.5 s to send its banner, the unread log backlog and its
        prompt - twice. A command issued during that is swallowed, and the leftover second
        prompt would be returned as the first command's output.
        """
        deadline = max(timeout, 20.0)
        self._send_line("")
        for _ in range(4):
            # Wait for a prompt, not for a gap. The banner and the unread log backlog
            # arrive in bursts with pauses between them, so stopping at the first pause
            # leaves the console still talking - and it swallows the next command.
            text, saw_prompt = self._collect(deadline, deadline)
            if saw_prompt:
                self._collect(1.0, 0.5)  # the console repeats its prompt; drop that
                break
            if _password_question(text.rstrip(" ")):
                self._skip_password_change()
                continue
            if LICENSE_QUESTION in text.lower():
                self._send_line("n")  # decline to have the licence printed, then carry on
                continue
            break
        self._primed = True

    def _skip_password_change(self) -> None:
        """Answer "new password>" with Ctrl-C: the password stays exactly as it is.

        Typing anything else there sets it. On 2026-09-21 two file-transfer commands went
        in as "new password" and "repeat", and only their being different kept the admin
        password from changing.
        """
        data = self._packet(MessageType.DATA)
        data.data = CTRL_C
        self._send(data)

    def run(self, line: str, *, quiet: float = 0.4, timeout: float = 10.0,
            confirm: bool = True) -> str:
        """Send one CLI line and return its output, without echo or prompt."""
        if not self._authenticated:
            raise MacTelnetError("not authenticated")
        if not self._primed:
            self._prime(timeout)

        self._send_line(line)
        # The prompt delimits the reply, the timeout bounds it; a pause in between means
        # nothing, because the console streams long output in bursts.
        raw, saw_prompt = self._collect(timeout, max(quiet, timeout), expect=line.strip())
        text = sanitize(raw)
        error = find_error(text)
        if error is not None:
            raise MacTelnetError(f"{line}: {error}")
        if confirm and not saw_prompt:
            # Silence is not success: the console swallows input while it is still
            # printing its banner, and calling that "done" is how a bootstrap address
            # was once reported as set when the device had added nothing. Callers that
            # verify the outcome by asking the device pass confirm=False instead, because
            # an unreadable prompt says nothing about whether the command ran.
            raise MacTelnetError(
                f"{line}: the console did not return a prompt, so the command cannot be "
                f"confirmed as executed"
            )
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
