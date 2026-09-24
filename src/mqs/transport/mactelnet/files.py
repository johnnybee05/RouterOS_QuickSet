"""File transfer over the MAC-Telnet console, for a device with no usable IP path.

Winbox moves files over MAC with a protocol of its own. This does the same with plain
RouterOS commands typed into the console that connect-by-MAC already logs in to, so it
works whenever that login works - no IP address, SSH service or firewall rule involved.

* **upload** - the bytes are built up in a global variable a line at a time, written as
  printable characters and ``\\XX`` escapes, then stored with ``/file add ... contents=``;
* **download** - ``/file read`` one chunk at a time, ``:convert``-ed to base64 so that a
  binary ``.backup`` survives the terminal intact.

Every transfer is checked end to end: an upload is read back and compared byte for byte,
a download must come to the size the device reports. A console that swallowed or garbled
a line therefore fails the transfer instead of leaving a damaged file behind.

The commands are verified on RouterOS 7.20.7 (docs/DIALECT_NOTES.md). They do not exist
on RouterOS 6, where this strategy fails and the next one is tried.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import re
import secrets
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Protocol

from mqs.transport.base import TransportError
from mqs.transport.upload import UploadStrategy

# Printable characters that go into a console string as they are. Everything else is
# written as \XX: '"', '\' and '$' mean something inside a string, '?' opens the console's
# help even inside quotes, and control and non-ASCII bytes are not typed at all.
_PLAIN = frozenset(
    b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    b" -_.,:;=/+*()[]{}<>!@#%^&|~'"
)
# Characters of one command line. Kept well under one Ethernet frame so that every line
# travels as a single datagram, and escapes are never split across lines.
LINE_BUDGET = 800
# Bytes per /file read. Base64 makes the console output a third larger than this.
READ_CHUNK = 16384
# Largest upload: meant for scripts of a few KiB. The bytes go through one variable a
# line at a time, and the device logs the whole `/file add contents=` (2026-09-23), so a
# RouterOS package (~10 MB) must take another way.
MAX_UPLOAD = 64 * 1024
# Markers around downloaded data. The command writes them in two halves ("MQ" . "S<"),
# so the echo of the command never contains the joined marker and cannot be mistaken
# for its output.
_DATA_RE = re.compile(r"MQS<([A-Za-z0-9+/=]*)>MQS")


class Console(Protocol):
    def run(self, line: str, *, quiet: float = ..., timeout: float = ...,
            confirm: bool = ...) -> str: ...


class ConsoleTransferError(TransportError):
    pass


def escape_byte(value: int) -> str:
    """One byte as it goes inside a double-quoted RouterOS string.

    Hex escapes must be upper case: verified on 7.20.7, "\\3F" is a question mark and
    "\\3f" a syntax error.
    """
    return chr(value) if value in _PLAIN else f"\\{value:02X}"


def string_pieces(data: bytes, budget: int) -> Iterator[str]:
    """``data`` as escaped string pieces of at most ``budget`` characters each."""
    piece: list[str] = []
    size = 0
    for value in data:
        token = escape_byte(value)
        if size + len(token) > budget and piece:
            yield "".join(piece)
            piece, size = [], 0
        piece.append(token)
        size += len(token)
    if piece:
        yield "".join(piece)


def quote_name(name: str) -> str:
    return '"' + "".join(escape_byte(b) for b in name.encode("utf-8")) + '"'


class ConsoleFileTransfer:
    """Upload and download over a logged-in console (a MacTelnetSession)."""

    def __init__(self, console: Console, *, line_budget: int = LINE_BUDGET,
                 read_chunk: int = READ_CHUNK, timeout: float = 30.0,
                 token: Callable[[], str] = lambda: secrets.token_hex(4)) -> None:
        self.console = console
        self.line_budget = line_budget
        self.read_chunk = read_chunk
        self.timeout = timeout
        self._token = token

    def _run(self, line: str) -> str:
        return self.console.run(line, timeout=self.timeout)

    # ------------------------------------------------------------ download

    def size(self, remote_path: str) -> int:
        text = self._run(f":put [/file get [find name={quote_name(remote_path)}] size]")
        value = text.strip().splitlines()[-1].strip() if text.strip() else ""
        if not value.isdigit():
            raise ConsoleTransferError(f"{remote_path}: the device reported no size ({text!r})")
        return int(value)

    def read(self, remote_path: str) -> bytes:
        size = self.size(remote_path)
        name = quote_name(remote_path)
        data = bytearray()
        while len(data) < size:
            command = (
                f':put ("MQ" . "S<" . [:convert ([/file read file={name} '
                f'offset={len(data)} chunk-size={self.read_chunk} as-value]->"data") '
                f'to=base64] . ">MQ" . "S")'
            )
            output = "".join(self._run(command).split())
            match = _DATA_RE.search(output)
            if match is None:
                raise ConsoleTransferError(f"{remote_path}: no data at offset {len(data)}")
            try:
                chunk = base64.b64decode(match.group(1), validate=True)
            except binascii.Error as exc:
                raise ConsoleTransferError(
                    f"{remote_path}: damaged data at offset {len(data)}") from exc
            if not chunk:
                break  # the file got shorter than it said; the size check reports it
            data += chunk
        if len(data) != size:
            raise ConsoleTransferError(
                f"{remote_path}: read {len(data)} bytes, the device has {size}")
        return bytes(data)

    def download(self, remote_path: str, local_path: Path) -> None:
        local_path.write_bytes(self.read(remote_path))

    # ------------------------------------------------------------ upload

    def upload(self, data: bytes, remote_path: str) -> None:
        variable = f"mqsxfer{self._token()}"
        name = quote_name(remote_path)
        declare = f":global {variable}; "
        # The variable holds the whole file, admin password included, so it goes as soon
        # as the file is written - also when the transfer failed half way.
        forget = f'/system script environment remove [find name="{variable}"]'
        try:
            self._run(f':global {variable} ""')
            for piece in string_pieces(data, self.line_budget):
                self._run(f'{declare}:set {variable} (${variable} . "{piece}")')
            # /file add refuses to overwrite: "failure: file already exists" (7.20.7).
            self._run(f"/file remove [find name={name}]")
            self._run(f"{declare}/file add name={name} contents=${variable}")
        except BaseException:
            with contextlib.suppress(TransportError):
                self._run(forget)
            raise
        self._run(forget)
        written = self.read(remote_path)
        if written != data:
            raise ConsoleTransferError(
                f"{remote_path}: the device holds {len(written)} bytes that differ from the "
                f"{len(data)} uploaded")


class MacTelnetStrategy:
    """FileUploader strategy over a console that is opened on first use."""

    name = UploadStrategy.MAC_TELNET

    def __init__(self, console: Callable[[], Console], **transfer_options: object) -> None:
        self.console = console
        self.transfer_options = transfer_options

    def _transfer(self) -> ConsoleFileTransfer:
        return ConsoleFileTransfer(self.console(), **self.transfer_options)  # type: ignore[arg-type]

    def upload(self, data: bytes, remote_path: str) -> None:
        if len(data) > MAX_UPLOAD:
            raise ConsoleTransferError(f"{len(data)} bytes is more than the console moves "
                                       f"(at most {MAX_UPLOAD})")
        self._transfer().upload(data, remote_path)

    def download(self, remote_path: str, local_path: Path) -> None:
        self._transfer().download(remote_path, local_path)


__all__ = [
    "LINE_BUDGET",
    "MAX_UPLOAD",
    "READ_CHUNK",
    "Console",
    "ConsoleFileTransfer",
    "ConsoleTransferError",
    "MacTelnetStrategy",
    "escape_byte",
    "quote_name",
    "string_pieces",
]
