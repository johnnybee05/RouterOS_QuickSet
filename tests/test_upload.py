from __future__ import annotations

import urllib.error
import urllib.request
from types import SimpleNamespace
from typing import ClassVar

import pytest

from mqs.transport.netifs import route_source_address
from mqs.transport.upload import (
    FileUploader,
    HttpFetchStrategy,
    ScpStrategy,
    SftpStrategy,
    UploadFailed,
    UploadStrategy,
)

DATA = b"/system identity\nset name=R1\n"


class FakeHandle:
    def __init__(self, store, path):
        self.store, self.path = store, path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def write(self, data):
        self.store[self.path] = self.store.get(self.path, b"") + data


class FakeSftp:
    def __init__(self, store, truncate=False):
        self.store, self.truncate, self.closed = store, truncate, False

    def open(self, path, mode):
        assert mode == "wb"
        return FakeHandle(self.store, path)

    def stat(self, path):
        size = len(self.store[path]) - (1 if self.truncate else 0)
        return SimpleNamespace(st_size=size)

    def get(self, remote, local):
        with open(local, "wb") as handle:
            handle.write(self.store[remote])

    def close(self):
        self.closed = True


class FakeSsh:
    def __init__(self, sftp_error=None, truncate=False):
        self.store: dict[str, bytes] = {}
        self.sftp_error, self.truncate = sftp_error, truncate
        self.sftps: list[FakeSftp] = []

    def open_sftp(self):
        if self.sftp_error:
            raise self.sftp_error
        sftp = FakeSftp(self.store, self.truncate)
        self.sftps.append(sftp)
        return sftp

    def paramiko_transport(self):
        return "paramiko-transport"


class FakeScpClient:
    store: ClassVar[dict[str, bytes]] = {}
    fail = False

    def __init__(self, transport, socket_timeout):
        assert transport == "paramiko-transport"
        self.timeout = socket_timeout

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def putfo(self, fileobj, remote_path):
        if FakeScpClient.fail:
            raise OSError("scp: not supported")
        FakeScpClient.store[remote_path] = fileobj.read()

    def get(self, remote, local):
        with open(local, "wb") as handle:
            handle.write(FakeScpClient.store[remote])


class FetchingTransport:
    """Behaves like a device running `/tool fetch`: downloads the URL for real."""

    def __init__(self, fetch=True):
        self.fetch = fetch
        self.calls = []
        self.downloaded = None

    def execute(self, path, /, **args):
        self.calls.append((path, args))
        if self.fetch:
            with urllib.request.urlopen(args["url"], timeout=5) as response:
                self.downloaded = response.read()
            wrong = args["url"].rsplit("/", 2)[0] + "/wrong/token"
            with pytest.raises(urllib.error.HTTPError):
                urllib.request.urlopen(wrong, timeout=5)
        return []


def test_sftp_upload_download_and_size_check(tmp_path):
    ssh = FakeSsh()
    SftpStrategy(ssh).upload(DATA, "flash/q.rsc")
    assert ssh.store["flash/q.rsc"] == DATA and ssh.sftps[0].closed
    local = tmp_path / "backup.rsc"
    SftpStrategy(ssh).download("flash/q.rsc", local)
    assert local.read_bytes() == DATA
    with pytest.raises(OSError, match="size mismatch"):
        SftpStrategy(FakeSsh(truncate=True)).upload(DATA, "q.rsc")


def test_scp_upload_and_download(tmp_path):
    FakeScpClient.store, FakeScpClient.fail = {}, False
    strategy = ScpStrategy(FakeSsh(), client_factory=FakeScpClient, timeout=12)
    strategy.upload(DATA, "q.rsc")
    assert FakeScpClient.store["q.rsc"] == DATA
    strategy.download("q.rsc", tmp_path / "x")
    assert (tmp_path / "x").read_bytes() == DATA


def test_http_fetch_serves_file_once_on_random_path():
    transport = FetchingTransport()
    HttpFetchStrategy(transport, "127.0.0.1").upload(DATA, "flash/q.rsc")
    assert transport.downloaded == DATA
    path, args = transport.calls[0]
    assert path == "/tool/fetch"
    assert args["dst_path"] == "flash/q.rsc"
    assert args["url"].startswith("http://127.0.0.1:") and args["url"].endswith("/q.rsc")


def test_http_fetch_detects_no_download():
    with pytest.raises(OSError, match="did not download"):
        HttpFetchStrategy(FetchingTransport(fetch=False), "127.0.0.1").upload(DATA, "q.rsc")


def test_uploader_fallback_order():
    FakeScpClient.store, FakeScpClient.fail = {}, False
    ssh = FakeSsh(sftp_error=OSError("sftp subsystem not available"))
    uploader = FileUploader([SftpStrategy(ssh), ScpStrategy(ssh, client_factory=FakeScpClient)])
    result = uploader.upload(DATA, "q.rsc")
    assert result.strategy == UploadStrategy.SCP and result.remote_path == "q.rsc"
    assert result.attempts[0][0] == UploadStrategy.SFTP
    assert "sftp subsystem" in result.attempts[0][1]


def test_uploader_all_fail():
    FakeScpClient.fail = True
    ssh = FakeSsh(sftp_error=OSError("no sftp"))
    strategies = [SftpStrategy(ssh), ScpStrategy(ssh, client_factory=FakeScpClient),
                  HttpFetchStrategy(FetchingTransport(fetch=False), "127.0.0.1")]
    with pytest.raises(UploadFailed) as err:
        FileUploader(strategies).upload(DATA, "q.rsc")
    assert [s for s, _ in err.value.attempts] == [UploadStrategy.SFTP, UploadStrategy.SCP,
                                                  UploadStrategy.HTTP_FETCH]
    result = FileUploader(strategies, allow_line_by_line=True).upload(DATA, "q.rsc")
    assert result.strategy == UploadStrategy.LINE_BY_LINE and result.remote_path is None
    assert len(result.attempts) == 3
    FakeScpClient.fail = False


def test_uploader_download_fallback(tmp_path):
    FakeScpClient.store, FakeScpClient.fail = {"b.backup": b"BIN"}, False
    ssh = FakeSsh(sftp_error=OSError("no sftp"))
    uploader = FileUploader([SftpStrategy(ssh), HttpFetchStrategy(FetchingTransport(), "x"),
                             ScpStrategy(ssh, client_factory=FakeScpClient)])
    assert uploader.download("b.backup", tmp_path / "b.backup") == UploadStrategy.SCP
    with pytest.raises(UploadFailed):
        FileUploader([SftpStrategy(ssh)]).download("b.backup", tmp_path / "c")


def test_route_source_address():
    assert route_source_address("127.0.0.1") == "127.0.0.1"
