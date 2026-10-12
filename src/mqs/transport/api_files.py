"""File download over the API connection the session already has.

SFTP and SCP need an SSH login of their own, and that login can be refused where the
API took the same account: RouterOS 7 takes no password over SSH from a user that has an
SSH key (`/ip ssh password-authentication=yes-if-no-key`), so on 2026-10-12 an RB4011
kept both pre-apply files although the session was in. This reads the file over the API
instead, with no second login at all.

A plain `/file/read` over the API does not do: librouteros decodes every reply as ASCII
and drops what is not, so a `.backup` came back without its first four bytes
(`88 ac a1 b1`, hEX S 7.23.5, 2026-10-12) and nothing said so. The device therefore
`:convert`s each chunk to base64 inside `/execute ... as-string`, which returns the
script's output as `ret` - the same commands the MAC-Telnet console types
(`ConsoleFileTransfer`), sent one at a time through `/execute` instead of a terminal.

`/execute` reports a failing script in `ret` too, as text and not as an API error
("input does not match any value of file", "value of chunk-size out of range
(1..32768)"), which is why the data travels between markers and the whole download must
come to the size the device reports. RouterOS 6 has neither `/file read` nor `:convert`;
there this strategy fails and the next one is tried.
"""

from __future__ import annotations

from pathlib import Path

from mqs.transport.base import Transport
from mqs.transport.mactelnet.files import ConsoleFileTransfer
from mqs.transport.upload import UploadStrategy

# The largest chunk-size `/file read` takes (7.23.5: "out of range (1..32768)"). Base64
# makes the reply a third larger, which one API word carries without trouble.
READ_CHUNK = 32768


class ExecuteConsole:
    """A console over the API: each line is a script run by `/execute ... as-string`."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def run(self, line: str, *, quiet: float = 0.0, timeout: float = 30.0,
            confirm: bool = False) -> str:
        with self.transport.reply_timeout(timeout):
            reply = self.transport.execute("/execute", script=line, as_string="")
        return "".join(row.get("ret", "") for row in reply)


class ApiFileStrategy:
    """FileUploader strategy that only downloads; uploads go by SFTP, SCP or fetch."""

    name = UploadStrategy.API

    def __init__(self, transport: Transport, *, read_chunk: int = READ_CHUNK) -> None:
        self.transport = transport
        self.read_chunk = read_chunk

    def download(self, remote_path: str, local_path: Path) -> None:
        transfer = ConsoleFileTransfer(ExecuteConsole(self.transport),
                                       read_chunk=self.read_chunk)
        transfer.download(remote_path, local_path)


__all__ = ["READ_CHUNK", "ApiFileStrategy", "ExecuteConsole"]
