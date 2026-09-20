"""File transfer to and from RouterOS (spec 4.6).

The RouterOS API cannot transfer files and SFTP is not reliable across versions, so
uploads try several strategies in order and fall back automatically:

1. SFTP over the SSH connection,
2. SCP over the same paramiko transport,
3. a temporary local HTTP server + `/tool fetch` on the device,
4. no file at all: the caller executes the script line by line (IMPORT mode only).
"""

from __future__ import annotations

import http.server
import io
import secrets
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import scp

from mqs.transport.base import Transport, TransportError


class UploadStrategy(StrEnum):
    SFTP = "sftp"
    SCP = "scp"
    HTTP_FETCH = "http-fetch"
    LINE_BY_LINE = "line-by-line"


class SshLike(Protocol):
    def open_sftp(self) -> Any: ...

    def paramiko_transport(self) -> Any: ...


@dataclass(frozen=True)
class UploadResult:
    strategy: UploadStrategy
    remote_path: str | None  # None for LINE_BY_LINE
    attempts: tuple[tuple[UploadStrategy, str], ...] = ()


class UploadFailed(TransportError):
    def __init__(self, attempts: Sequence[tuple[UploadStrategy, str]]) -> None:
        details = "; ".join(f"{s.value}: {e}" for s, e in attempts)
        super().__init__(f"file transfer failed ({details})")
        self.attempts = tuple(attempts)


class SftpStrategy:
    name = UploadStrategy.SFTP

    def __init__(self, ssh: SshLike) -> None:
        self.ssh = ssh

    def upload(self, data: bytes, remote_path: str) -> None:
        sftp = self.ssh.open_sftp()
        try:
            with sftp.open(remote_path, "wb") as handle:
                handle.write(data)
            size = sftp.stat(remote_path).st_size
            if size != len(data):
                raise OSError(f"size mismatch after upload: {size} != {len(data)}")
        finally:
            sftp.close()

    def download(self, remote_path: str, local_path: Path) -> None:
        sftp = self.ssh.open_sftp()
        try:
            sftp.get(remote_path, str(local_path))
        finally:
            sftp.close()


class ScpStrategy:
    name = UploadStrategy.SCP

    def __init__(self, ssh: SshLike, client_factory: Callable[..., Any] = scp.SCPClient,
                 timeout: float = 30.0) -> None:
        self.ssh = ssh
        self.client_factory = client_factory
        self.timeout = timeout

    def upload(self, data: bytes, remote_path: str) -> None:
        with self.client_factory(self.ssh.paramiko_transport(),
                                 socket_timeout=self.timeout) as client:
            client.putfo(io.BytesIO(data), remote_path)

    def download(self, remote_path: str, local_path: Path) -> None:
        with self.client_factory(self.ssh.paramiko_transport(),
                                 socket_timeout=self.timeout) as client:
            client.get(remote_path, str(local_path))


class _OneFileServer(http.server.HTTPServer):
    def __init__(self, address: tuple[str, int], url_path: str, data: bytes) -> None:
        self.url_path = url_path
        self.data = data
        self.served = 0
        super().__init__(address, _OneFileHandler)


class _OneFileHandler(http.server.BaseHTTPRequestHandler):
    server: _OneFileServer

    def do_GET(self) -> None:
        if self.path != self.server.url_path:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(self.server.data)))
        self.end_headers()
        self.wfile.write(self.server.data)
        self.server.served += 1

    def log_message(self, format: str, *args: Any) -> None:
        return  # progress is logged by the apply flow, not by http.server


class HttpFetchStrategy:
    """Serve the file once from this PC and let the device download it.

    Windows Firewall may block the inbound connection; the failure is reported and the
    next strategy is tried.
    """

    name = UploadStrategy.HTTP_FETCH

    def __init__(self, transport: Transport, local_address: str) -> None:
        self.transport = transport
        self.local_address = local_address

    def upload(self, data: bytes, remote_path: str) -> None:
        filename = remote_path.rsplit("/", 1)[-1]
        url_path = f"/{secrets.token_urlsafe(16)}/{filename}"
        server = _OneFileServer((self.local_address, 0), url_path, data)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            url = f"http://{self.local_address}:{port}{url_path}"
            self.transport.execute("/tool/fetch", url=url, dst_path=remote_path)
            if server.served == 0:
                raise OSError("device did not download the file")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)


class FileUploader:
    def __init__(self, strategies: Sequence[Any], *, allow_line_by_line: bool = False,
                 release: Callable[[], None] | None = None) -> None:
        self.strategies = list(strategies)
        self.allow_line_by_line = allow_line_by_line
        self._release = release

    def release(self) -> None:
        """Close a transfer connection this uploader opened for itself.

        On 7.20.7 the same `/system/reset-configuration run-after-reset=...` call is
        accepted from a process that holds no SSH session and answered "cannot use file"
        from the application, which holds one. That correlation is not a proven cause
        (see docs/DIALECT_NOTES.md), but nothing is gained by keeping the connection, so
        it goes before the reset is issued.
        """
        if self._release is not None:
            self._release()
            self._release = None

    def upload(self, data: bytes, remote_path: str) -> UploadResult:
        attempts: list[tuple[UploadStrategy, str]] = []
        for strategy in self.strategies:
            try:
                strategy.upload(data, remote_path)
            except Exception as exc:
                attempts.append((strategy.name, f"{type(exc).__name__}: {exc}"))
                continue
            return UploadResult(strategy.name, remote_path, tuple(attempts))
        if self.allow_line_by_line:
            return UploadResult(UploadStrategy.LINE_BY_LINE, None, tuple(attempts))
        raise UploadFailed(attempts)

    def download(self, remote_path: str, local_path: Path) -> UploadStrategy:
        attempts: list[tuple[UploadStrategy, str]] = []
        for strategy in self.strategies:
            if not hasattr(strategy, "download"):
                continue
            try:
                strategy.download(remote_path, local_path)
            except Exception as exc:
                attempts.append((strategy.name, f"{type(exc).__name__}: {exc}"))
                continue
            return strategy.name
        raise UploadFailed(attempts)
