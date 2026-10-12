"""Downloading over the API session: base64 made on the device, behind markers."""

from __future__ import annotations

import base64
import re

import pytest

from fake_transport import FakeTransport
from mqs.transport.api_files import READ_CHUNK, ApiFileStrategy
from mqs.transport.mactelnet.files import ConsoleTransferError
from mqs.transport.upload import FileUploader, UploadFailed, UploadStrategy

# A .backup starts with bytes that are not ASCII, which a plain /file/read over the API
# lost without a word (hEX S 7.23.5, 2026-10-12).
BACKUP = bytes.fromhex("88aca1b1424d00000a000000") + bytes(range(256)) * 300


class ExecuteDevice(FakeTransport):
    """Answers `/execute ... as-string` the way 7.23.5 did, failing scripts included."""

    def __init__(self, files: dict[str, bytes]) -> None:
        super().__init__()
        self.files = files
        self.scripts: list[str] = []

    def execute(self, path, /, **args):
        if path != "/execute":
            return super().execute(path, **args)
        assert args.get("as_string") == "", "without as-string the reply is a job id"
        script = str(args["script"])
        self.scripts.append(script)
        name = re.search(r'name="([^"]*)"|file="([^"]*)"', script)
        data = self.files.get(next(g for g in name.groups() if g is not None))
        if data is None:
            return [{"ret": "input does not match any value of file (/file/read (file); "
                            "line 1)"}]
        if "/file get" in script:
            return [{"ret": str(len(data))}]
        offset = int(re.search(r"offset=(\d+)", script).group(1))
        size = int(re.search(r"chunk-size=(\d+)", script).group(1))
        if size > READ_CHUNK:
            return [{"ret": "value of chunk-size out of range (1..32768) (/file/read "
                            "(chunk-size); line 1)"}]
        chunk = base64.b64encode(data[offset:offset + size]).decode()
        return [{"ret": f"MQS<{chunk}>MQS"}]


def test_a_backup_comes_over_byte_for_byte(tmp_path):
    device = ExecuteDevice({"flash/pre-quickset-1.backup": BACKUP})
    local = tmp_path / "pre-quickset-1.backup"
    ApiFileStrategy(device).download("flash/pre-quickset-1.backup", local)
    assert local.read_bytes() == BACKUP
    reads = [s for s in device.scripts if "/file read" in s]
    assert len(reads) == -(-len(BACKUP) // READ_CHUNK), "chunks as big as the device takes"
    assert all("to=base64" in s for s in reads)


def test_an_error_the_device_returns_as_text_fails_the_download(tmp_path):
    """/execute puts a failing script's message in `ret`, not in an API error."""
    with pytest.raises(ConsoleTransferError, match="reported no size"):
        ApiFileStrategy(ExecuteDevice({})).download("gone.backup", tmp_path / "gone.backup")
    assert not (tmp_path / "gone.backup").exists()


def test_a_chunk_the_device_refuses_fails_instead_of_writing_garbage(tmp_path):
    strategy = ApiFileStrategy(ExecuteDevice({"a.backup": BACKUP}), read_chunk=READ_CHUNK + 1)
    with pytest.raises(ConsoleTransferError, match="no data at offset 0"):
        strategy.download("a.backup", tmp_path / "a.backup")


def test_uploads_pass_it_by():
    class Recorder:
        name = UploadStrategy.SFTP

        def __init__(self):
            self.got = []

        def upload(self, data, remote_path):
            self.got.append(remote_path)

    sftp = Recorder()
    result = FileUploader([ApiFileStrategy(ExecuteDevice({})), sftp]).upload(b"x", "a.rsc")
    assert result.strategy == UploadStrategy.SFTP and result.attempts == ()
    assert sftp.got == ["a.rsc"]


def test_a_failed_api_download_falls_through_to_the_next_way(tmp_path):
    class Sftp:
        name = UploadStrategy.SFTP

        def download(self, remote_path, local_path):
            local_path.write_bytes(b"from sftp")

    uploader = FileUploader([ApiFileStrategy(ExecuteDevice({})), Sftp()])
    assert uploader.download("a.backup", tmp_path / "a.backup") == UploadStrategy.SFTP
    with pytest.raises(UploadFailed, match="api: ConsoleTransferError"):
        FileUploader([ApiFileStrategy(ExecuteDevice({}))]).download("a.backup",
                                                                     tmp_path / "b")
