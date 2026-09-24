"""File transfer over the MAC-Telnet console, against a console that decodes on its own."""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

from mqs.transport.mactelnet.client import MacTelnetError
from mqs.transport.mactelnet.files import (
    LINE_BUDGET,
    MAX_UPLOAD,
    READ_CHUNK,
    ConsoleFileTransfer,
    ConsoleTransferError,
    MacTelnetStrategy,
    escape_byte,
    quote_name,
    string_pieces,
)
from mqs.transport.upload import FileUploader, UploadStrategy

_STRING = r'"(?:[^"\\]|\\.)*"'


def ros_string(literal: str) -> bytes:
    """Decode a double-quoted RouterOS string the way 7.20.7 was seen to.

    Written from the device's behaviour, not from the encoder: upper-case \\XX is one
    byte, lower-case hex is a syntax error, and a bare '"', '$' or '?' - or any byte a
    terminal would act on - cannot be typed into a string at all.
    """
    assert literal[0] == literal[-1] == '"'
    body, out, i = literal[1:-1], bytearray(), 0
    while i < len(body):
        char = body[i]
        if char == "\\":
            pair = body[i + 1:i + 3]
            if re.fullmatch(r"[0-9A-F]{2}", pair):
                out.append(int(pair, 16))
                i += 3
                continue
            if body[i + 1] in '"\\$?':
                out.append(ord(body[i + 1]))
                i += 2
                continue
            raise MacTelnetError(f"syntax error in {literal[:40]}")
        if char in '"$?' or not " " <= char <= "~":
            raise MacTelnetError(f"cannot be typed: {char!r}")
        out.append(ord(char))
        i += 1
    return bytes(out)


class FakeConsole:
    """The handful of RouterOS console commands the transfer uses."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.globals: dict[str, bytes] = {}
        self.lines: list[str] = []
        self.fail_on: str | None = None
        self.corrupt_add = False
        self.short_reads = False
        self.damage_reads = False

    def run(self, line: str, *, quiet: float = 0.4, timeout: float = 10.0,
            confirm: bool = True) -> str:
        self.lines.append(line)
        if self.fail_on is not None and self.fail_on in line:
            raise MacTelnetError(f"{line}: failure: the console went away")
        if match := re.fullmatch(r':global (\w+) ""', line):
            self.globals[match[1]] = b""
            return ""
        if match := re.fullmatch(rf":global (\w+); :set \1 \(\$\1 \. ({_STRING})\)", line):
            self.globals[match[1]] += ros_string(match[2])
            return ""
        if match := re.fullmatch(rf"/file remove \[find name=({_STRING})\]", line):
            self.files.pop(ros_string(match[1]).decode(), None)
            return ""
        if match := re.fullmatch(rf":global (\w+); /file add name=({_STRING}) contents=\$\1",
                                 line):
            name = ros_string(match[2]).decode()
            if name in self.files:
                raise MacTelnetError(f"{line}: failure: file already exists")
            data = self.globals[match[1]]
            self.files[name] = data[:-1] + b"#" if self.corrupt_add else data
            return ""
        if match := re.fullmatch(r'/system script environment remove \[find name="(\w+)"\]',
                                 line):
            self.globals.pop(match[1], None)
            return ""
        if match := re.fullmatch(rf":put \[/file get \[find name=({_STRING})\] size\]", line):
            name = ros_string(match[1]).decode()
            if name not in self.files:
                raise MacTelnetError(f"{line}: no such item")
            return str(len(self.files[name]))
        read = (rf':put \("MQ" \. "S<" \. \[:convert \(\[/file read file=({_STRING}) '
                r'offset=(\d+) chunk-size=(\d+) as-value\]->"data"\) to=base64\] \. '
                r'">MQ" \. "S"\)')
        if match := re.fullmatch(read, line):
            size = int(match[3])
            if not 1 <= size <= 32768:  # the limit 7.20.7 reports
                raise MacTelnetError(f"{line}: value of chunk-size out of range (1..32768)")
            data = self.files[ros_string(match[1]).decode()]
            offset = int(match[2])
            chunk = data[offset:offset + size]
            if self.short_reads:
                chunk = chunk[:-1]
            text = base64.b64encode(chunk).decode()
            if self.damage_reads:
                text = text[:-2] + "!"
            # The device wraps output at 80 columns, and the tail of a scrolled echo can
            # survive the stripping - neither may confuse the reader.
            wrapped = "\n".join(text[i:i + 80] for i in range(0, len(text), 80)) or ""
            return f'< chunk-size={size} as-value]->"data") to=base64] . ">MQ" . "S")\n' \
                   f"MQS<{wrapped}>MQS"
        raise MacTelnetError(f"{line}: bad command name")


PAYLOAD = ('# "quoted" $var ?help \\path\t[find] {a;b}\r\n/system identity set '
           'name="Přístupový bod – ž"\n').encode() * 30 + bytes(range(256))


def test_every_byte_value_goes_up_and_comes_back():
    console = FakeConsole()
    transfer = ConsoleFileTransfer(console)
    transfer.upload(PAYLOAD, "flash/quickset-x.rsc")
    assert console.files["flash/quickset-x.rsc"] == PAYLOAD
    assert transfer.read("flash/quickset-x.rsc") == PAYLOAD
    assert console.globals == {}, "the variable holding the file was left on the device"


def test_every_line_can_be_typed():
    """Nothing a terminal would act on, and short enough for one datagram."""
    console = FakeConsole()
    ConsoleFileTransfer(console).upload(PAYLOAD, "quickset-x.rsc")
    for line in console.lines:
        assert all(" " <= char <= "~" for char in line), line
        assert "?" not in line, "a question mark opens the console's help"
        assert len(line) <= LINE_BUDGET + 80


def test_an_existing_file_is_replaced():
    console = FakeConsole()
    console.files["quickset-x.rsc"] = b"old"
    ConsoleFileTransfer(console).upload(b"new", "quickset-x.rsc")
    assert console.files["quickset-x.rsc"] == b"new"


def test_a_large_file_is_read_in_chunks(tmp_path: Path):
    console = FakeConsole()
    backup = bytes(range(256)) * 280  # about three times the chunk size
    console.files["pre-quickset.backup"] = backup
    ConsoleFileTransfer(console).download("pre-quickset.backup", tmp_path / "b.backup")
    assert (tmp_path / "b.backup").read_bytes() == backup
    offsets = [int(m[1]) for line in console.lines if (m := re.search(r"offset=(\d+)", line))]
    assert offsets == list(range(0, len(backup), READ_CHUNK))


def test_the_chunk_stays_within_what_the_device_allows():
    assert READ_CHUNK <= 32768  # "value of chunk-size out of range (1..32768)" on 7.20.7


def test_a_short_read_is_an_error():
    console = FakeConsole()
    console.files["x.backup"] = b"0123456789"
    console.short_reads = True
    with pytest.raises(ConsoleTransferError, match="read"):
        ConsoleFileTransfer(console).read("x.backup")


def test_damaged_data_is_an_error():
    console = FakeConsole()
    console.files["x.backup"] = b"0123456789"
    console.damage_reads = True
    with pytest.raises(ConsoleTransferError):
        ConsoleFileTransfer(console).read("x.backup")


def test_an_upload_is_checked_by_reading_it_back():
    console = FakeConsole()
    console.corrupt_add = True
    with pytest.raises(ConsoleTransferError, match="differ"):
        ConsoleFileTransfer(console).upload(PAYLOAD, "quickset-x.rsc")


def test_a_failed_upload_still_forgets_the_variable():
    """It holds the whole script, the new admin password included."""
    console = FakeConsole()
    console.fail_on = "/file add"
    with pytest.raises(MacTelnetError, match="went away"):
        ConsoleFileTransfer(console).upload(PAYLOAD, "quickset-x.rsc")
    assert console.globals == {}


def test_escapes_are_upper_case_hex():
    assert escape_byte(ord("a")) == "a"
    assert escape_byte(ord("?")) == "\\3F"  # "\3f" is a syntax error on 7.20.7
    assert escape_byte(ord('"')) == "\\22"
    assert escape_byte(ord("$")) == "\\24"
    assert escape_byte(0x0A) == "\\0A"
    assert escape_byte(0xC3) == "\\C3"
    assert quote_name("flash/q.rsc") == '"flash/q.rsc"'


def test_pieces_never_split_an_escape():
    pieces = list(string_pieces(PAYLOAD, 10))
    assert all(len(piece) <= 10 for piece in pieces)
    assert b"".join(ros_string(f'"{piece}"') for piece in pieces) == PAYLOAD


def test_the_strategy_asks_for_the_console_only_when_it_moves_a_file(tmp_path: Path):
    console = FakeConsole()
    opened = []

    def open_console() -> FakeConsole:
        opened.append(1)
        return console

    uploader = FileUploader([MacTelnetStrategy(open_console)])
    assert opened == []
    result = uploader.upload(b"/system identity set name=R1\n", "quickset-x.rsc")
    assert result.strategy == UploadStrategy.MAC_TELNET
    assert uploader.download("quickset-x.rsc", tmp_path / "q.rsc") == UploadStrategy.MAC_TELNET
    assert (tmp_path / "q.rsc").read_bytes() == b"/system identity set name=R1\n"


def test_a_package_is_not_pushed_through_the_console():
    """~10 MB a line at a time, and every byte in the device log: never."""
    opened = []
    strategy = MacTelnetStrategy(lambda: opened.append(1) or FakeConsole())
    with pytest.raises(ConsoleTransferError, match="more than the console moves"):
        strategy.upload(bytes(MAX_UPLOAD + 1), "routeros-7.23.7-arm.npk")
    assert opened == [], "the console is not even opened"
