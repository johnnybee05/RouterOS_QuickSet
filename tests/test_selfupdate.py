"""Updating the application: nothing replaces the exe before it is verified, and the exe is
never left missing."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from mqs import selfupdate
from mqs.selfupdate import Release, UpdateCancelled, UpdateError

EXE_BYTES = b"MZ" + b"\x90" * 300_000
DIGEST = hashlib.sha256(EXE_BYTES).hexdigest()
ASSET_URL = "https://example.invalid/MikrotikQuickSetPlus.exe"


def release_json(**overrides):
    data = {
        "tag_name": "v0.13.0", "draft": False, "prerelease": False,
        "html_url": "https://github.com/x/y/releases/tag/v0.13.0", "body": "## Přidáno",
        "assets": [{"name": "MikrotikQuickSetPlus.exe", "size": len(EXE_BYTES),
                    "digest": f"sha256:{DIGEST}", "browser_download_url": ASSET_URL}],
    }
    data.update(overrides)
    return data


def release(**overrides) -> Release:
    values = {"version": "0.13.0", "notes": "", "page_url": "page", "asset_url": ASSET_URL,
              "asset_size": len(EXE_BYTES), "sha256": DIGEST}
    values.update(overrides)
    return Release(**values)


class Response(io.BytesIO):
    def __init__(self, data: bytes, headers=None):
        super().__init__(data)
        self.headers = headers or {}


def opener_for(data: bytes):
    requests = []

    def opener(request, timeout):
        requests.append(request)
        return Response(data, {"Content-Length": str(len(data))})

    opener.requests = requests
    return opener


@pytest.fixture
def exe(tmp_path: Path) -> Path:
    path = tmp_path / "MikrotikQuickSetPlus.exe"
    path.write_bytes(b"MZ old version")
    return path


# ------------------------------------------------------------------ the release

def test_release_from_github_json():
    found = selfupdate.release_from_json(release_json())
    assert found == Release("0.13.0", "## Přidáno", "https://github.com/x/y/releases/tag/v0.13.0",
                            ASSET_URL, len(EXE_BYTES), DIGEST)


def test_release_without_digest_or_exe_cannot_be_verified():
    no_digest = release_json(assets=[{"name": "MikrotikQuickSetPlus.exe", "size": 5,
                                      "browser_download_url": ASSET_URL}])
    assert selfupdate.release_from_json(no_digest).sha256 == ""
    bad_digest = release_json(assets=[{"name": "MikrotikQuickSetPlus.exe", "size": 5,
                                       "digest": "md5:abc", "browser_download_url": ASSET_URL}])
    assert selfupdate.release_from_json(bad_digest).sha256 == ""
    assert selfupdate.release_from_json(release_json(assets=[])).asset_url == ""


@pytest.mark.parametrize("data", [release_json(draft=True), release_json(prerelease=True),
                                  release_json(tag_name="nightly")])
def test_drafts_prereleases_and_odd_tags_are_not_offered(data):
    with pytest.raises(UpdateError):
        selfupdate.release_from_json(data)


@pytest.mark.parametrize(("candidate", "current", "newer"), [
    ("0.13.0", "0.12.1", True), ("0.12.10", "0.12.9", True), ("1.0", "0.99.99", True),
    ("0.12.1", "0.12.1", False), ("0.12.0", "0.12.1", False), ("junk", "0.12.1", False),
])
def test_is_newer_compares_numbers_not_text(candidate, current, newer):
    assert selfupdate.is_newer(candidate, current) is newer


def test_latest_release_asks_github(monkeypatch):
    monkeypatch.delenv(selfupdate.URL_ENV, raising=False)
    opener = opener_for(json.dumps(release_json()).encode())
    assert selfupdate.latest_release(opener=opener).version == "0.13.0"
    assert opener.requests[0].full_url == selfupdate.LATEST_URL
    assert opener.requests[0].get_header("User-agent").startswith("MikrotikQuickSetPlus/")


def test_latest_release_reports_network_trouble_as_update_error():
    def offline(request, timeout):
        raise OSError("no route to host")

    with pytest.raises(UpdateError, match="no route"):
        selfupdate.latest_release(opener=offline)
    with pytest.raises(UpdateError):
        selfupdate.latest_release(opener=opener_for(b"<html>rate limited</html>"))


# ------------------------------------------------------------------ download

def test_download_verifies_and_keeps_the_file_next_to_the_exe(exe):
    seen = []
    path = selfupdate.download(release(), exe, opener=opener_for(EXE_BYTES),
                               progress=lambda done, total: seen.append((done, total)))
    assert path == exe.with_name("MikrotikQuickSetPlus.update.exe")
    assert path.read_bytes() == EXE_BYTES
    assert seen[-1] == (len(EXE_BYTES), len(EXE_BYTES))
    assert exe.read_bytes() == b"MZ old version"  # nothing replaced yet


@pytest.mark.parametrize(("data", "message"), [
    (EXE_BYTES[:-1], "bytes"),  # cut short
    (b"MZ" + b"\x00" * (len(EXE_BYTES) - 2), "SHA-256"),  # same size, other content
], ids=["short", "other-content"])
def test_a_download_that_does_not_match_is_deleted(exe, data, message):
    with pytest.raises(UpdateError, match=message):
        selfupdate.download(release(), exe, opener=opener_for(data))
    assert not selfupdate.download_path(exe).exists()


def test_a_download_that_is_no_program_is_deleted(exe):
    page = b"<html>" + b" " * 100
    rel = release(asset_size=len(page), sha256=hashlib.sha256(page).hexdigest())
    with pytest.raises(UpdateError, match="Windows program"):
        selfupdate.download(rel, exe, opener=opener_for(page))
    assert not selfupdate.download_path(exe).exists()


def test_nothing_is_downloaded_without_a_digest(exe):
    opener = opener_for(EXE_BYTES)
    with pytest.raises(UpdateError, match="SHA-256"):
        selfupdate.download(release(sha256=""), exe, opener=opener)
    assert opener.requests == []


def test_a_cancelled_download_is_deleted(exe):
    with pytest.raises(UpdateCancelled):
        selfupdate.download(release(), exe, opener=opener_for(EXE_BYTES), cancelled=lambda: True)
    assert not selfupdate.download_path(exe).exists()


def test_a_broken_connection_is_an_update_error(exe):
    class Broken(Response):
        def read(self, size=-1):
            raise ConnectionResetError("reset by peer")

    with pytest.raises(UpdateError, match="reset by peer"):
        selfupdate.download(release(), exe, opener=lambda request, timeout: Broken(b""))
    assert not selfupdate.download_path(exe).exists()


# ------------------------------------------------------------------ self check

def test_self_check_runs_the_new_exe_on_its_own(exe):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    selfupdate.self_check(exe, run=run)
    command, kwargs = calls[0]
    assert command == [str(exe), "--self-check"]
    assert kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


@pytest.mark.parametrize("outcome", [subprocess.CompletedProcess([], 3),
                                     subprocess.TimeoutExpired("x", 1), OSError("blocked")])
def test_a_new_exe_that_does_not_start_is_refused(exe, outcome):
    def run(command, **kwargs):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    with pytest.raises(UpdateError, match="does not start"):
        selfupdate.self_check(exe, run=run)


def test_child_environment_drops_the_parents_onefile_state():
    env = selfupdate.child_environment({"PATH": "x", "_MEIPASS2": "C:\\t\\_MEI1",
                                        "_PYI_APPLICATION_HOME_DIR": "C:\\t\\_MEI1"})
    assert env == {"PATH": "x", "PYINSTALLER_RESET_ENVIRONMENT": "1"}


def test_restart_does_not_log_in_by_itself():
    command = selfupdate.restart_command(["--host", "10.0.0.1", "--connect"])
    assert command[-2:] == ["--host", "10.0.0.1"]


# ------------------------------------------------------------------ the swap

def test_install_puts_the_new_exe_in_place_and_keeps_the_old(exe):
    new = selfupdate.download_path(exe)
    new.write_bytes(EXE_BYTES)
    aside = selfupdate.install(new, exe)
    assert exe.read_bytes() == EXE_BYTES
    assert aside == exe.with_name("MikrotikQuickSetPlus.exe.old")
    assert aside.read_bytes() == b"MZ old version"
    assert not new.exists()


def test_install_gives_the_old_exe_back_when_the_new_one_cannot_take_its_name(exe, monkeypatch):
    new = selfupdate.download_path(exe)
    new.write_bytes(EXE_BYTES)
    real_replace = os.replace

    def replace(source, target):
        if Path(source) == new:
            raise PermissionError("locked by antivirus")
        real_replace(source, target)

    monkeypatch.setattr(selfupdate.os, "replace", replace)
    with pytest.raises(UpdateError, match="could not be replaced"):
        selfupdate.install(new, exe, sleep=lambda s: None)
    assert exe.read_bytes() == b"MZ old version"


def test_install_waits_out_a_short_lock(exe, monkeypatch):
    new = selfupdate.download_path(exe)
    new.write_bytes(EXE_BYTES)
    real_replace = os.replace
    failures = {"left": 3}

    def replace(source, target):
        if Path(source) == new and failures["left"]:
            failures["left"] -= 1
            raise PermissionError("scanning")
        real_replace(source, target)

    monkeypatch.setattr(selfupdate.os, "replace", replace)
    selfupdate.install(new, exe, sleep=lambda s: None)
    assert exe.read_bytes() == EXE_BYTES


def test_install_when_the_previous_old_exe_is_still_in_use(exe, monkeypatch):
    previous = selfupdate.old_path(exe)
    previous.write_bytes(b"MZ older still running")
    real_unlink = Path.unlink

    def unlink(self, *args, **kwargs):
        if self == previous:
            raise PermissionError("in use")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    new = selfupdate.download_path(exe)
    new.write_bytes(EXE_BYTES)
    aside = selfupdate.install(new, exe)
    assert aside != previous and aside.name.startswith("MikrotikQuickSetPlus.exe.old-")
    assert exe.read_bytes() == EXE_BYTES
    assert previous.read_bytes() == b"MZ older still running"


def test_cleanup_deletes_leftovers_but_not_the_exe(exe):
    for name in ("MikrotikQuickSetPlus.exe.old", "MikrotikQuickSetPlus.exe.old-42",
                 "MikrotikQuickSetPlus.update.exe"):
        exe.with_name(name).write_bytes(b"x")
    other = exe.with_name("notes.txt")
    other.write_text("keep")
    removed = selfupdate.cleanup(exe)
    assert len(removed) == 3
    assert exe.exists() and other.exists()
    assert sorted(p.name for p in exe.parent.iterdir()) == ["MikrotikQuickSetPlus.exe",
                                                             "notes.txt"]


def test_cleanup_at_start_leaves_old_exes_and_later_leaves_downloads(exe):
    """A start must not delete an update this run is downloading, nor the exe the previous
    version is still closing: each is taken only when it is safe."""
    old, download = selfupdate.old_path(exe), selfupdate.download_path(exe)
    old.write_bytes(b"x")
    download.write_bytes(b"x")
    assert selfupdate.cleanup(exe, old=False) == [download]
    download.write_bytes(b"x")  # this run's own download, under way
    assert selfupdate.cleanup(exe, download=False) == [old]
    assert download.exists()


def test_writable_directory(tmp_path):
    assert selfupdate.directory_writable(tmp_path)
    assert not selfupdate.directory_writable(tmp_path / "missing")
