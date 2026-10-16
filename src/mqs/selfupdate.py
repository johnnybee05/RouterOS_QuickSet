"""Updating the application itself from its GitHub releases.

The exe is one portable file, so updating it is replacing that file. Nothing about it is
trusted on its name, and nothing is replaced before the new file has proven itself:

1. The newest release is read from the GitHub API. GitHub publishes a SHA-256 digest of
   every release asset; a release without one is not installed — the user is sent to the
   release page instead, where they can decide for themselves.
2. The asset is downloaded next to the exe (same volume, so the swap is a rename) under a
   name of its own. Its size and SHA-256 must match what the release says, and it must be a
   Windows executable; anything else is deleted.
3. The downloaded exe is started once with ``--self-check``: it has to come up — unpack,
   import everything, create the Qt application — and exit 0. A build that does not start
   on this machine never replaces the one that does.
4. The running exe is renamed aside (Windows allows renaming a running image, not
   overwriting it) and the new one takes its name. If the second rename fails the first is
   undone, so the exe is never missing.
5. The new version is started only after the old window has closed and given its device
   back (`mqs.app.main`), and it deletes the old file once it is up.

A PyInstaller onefile exe started from another one inherits the parent's unpack directory
through the environment and would run the old code from it; ``PYINSTALLER_RESET_ENVIRONMENT``
makes it start as its own application (PyInstaller 6.9+, which build.ps1 uses).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mqs import __version__

log = logging.getLogger(__name__)

REPO = "johnnybee05/RouterOS_QuickSet"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
# Where to ask instead of GitHub: a test release served locally.
URL_ENV = "MQS_UPDATE_URL"
ASSET_NAME = "MikrotikQuickSetPlus.exe"
DOWNLOAD_SUFFIX = ".update.exe"  # an .exe, so Windows starts it for --self-check
OLD_SUFFIX = ".old"
SELF_CHECK_ARG = "--self-check"
SELF_CHECK_TIMEOUT_S = 120.0
CHECK_TIMEOUT_S = 15.0
DOWNLOAD_TIMEOUT_S = 60.0
CHUNK = 256 * 1024
# An antivirus scanning a file it has just seen written holds it open for a moment.
RENAME_ATTEMPTS = 10
RENAME_PAUSE_S = 0.5

_VERSION = re.compile(r"^v?(\d+(?:\.\d+)*)$")
_DIGEST = re.compile(r"^sha256:([0-9a-fA-F]{64})$")

Opener = Callable[..., Any]
Progress = Callable[[int, int], None]  # bytes so far, bytes expected


class UpdateError(Exception):
    """The update cannot go on; the message says why, for the user."""


class UpdateCancelled(UpdateError):
    """The user stopped the download."""


@dataclass(frozen=True)
class Release:
    version: str  # "0.13.0", without the "v"
    notes: str  # markdown, as written in the release
    page_url: str
    asset_url: str = ""  # "" when the release has no exe
    asset_size: int = 0
    sha256: str = ""  # lowercase hex; "" when GitHub gave no digest


def parse_version(text: str) -> tuple[int, ...] | None:
    match = _VERSION.match(text.strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def is_newer(candidate: str, current: str = __version__) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    return new is not None and old is not None and new > old


def release_from_json(data: Mapping[str, Any]) -> Release:
    if data.get("draft") or data.get("prerelease"):
        raise UpdateError("the newest release is a draft or a pre-release")
    tag = str(data.get("tag_name") or "")
    version = parse_version(tag)
    if version is None:
        raise UpdateError(f"the release tag {tag!r} is not a version")
    asset: Mapping[str, Any] = next(
        (a for a in data.get("assets") or [] if a.get("name") == ASSET_NAME), {})
    digest = _DIGEST.match(str(asset.get("digest") or ""))
    return Release(
        version=".".join(map(str, version)),
        notes=str(data.get("body") or ""),
        page_url=str(data.get("html_url") or RELEASES_PAGE),
        asset_url=str(asset.get("browser_download_url") or ""),
        asset_size=int(asset.get("size") or 0),
        sha256=digest.group(1).lower() if digest else "",
    )


def _request(url: str, accept: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={
        "Accept": accept, "User-Agent": f"MikrotikQuickSetPlus/{__version__}"})


def latest_release(*, opener: Opener = urllib.request.urlopen, url: str | None = None,
                   timeout: float = CHECK_TIMEOUT_S) -> Release:
    url = url or os.environ.get(URL_ENV) or LATEST_URL
    try:
        with opener(_request(url, "application/vnd.github+json"), timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise UpdateError(str(getattr(exc, "reason", None) or exc)) from exc
    if not isinstance(data, dict):
        raise UpdateError("unexpected answer from GitHub")
    return release_from_json(data)


# ------------------------------------------------------------------ where the exe is

def running_exe() -> Path | None:
    """The exe to replace, or None when running from the sources."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve()


def directory_writable(directory: Path) -> bool:
    probe = directory / f".mqs-write-test-{os.getpid()}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        return False
    return True


def download_path(exe: Path) -> Path:
    return exe.with_name(exe.stem + DOWNLOAD_SUFFIX)


def old_path(exe: Path) -> Path:
    return exe.with_name(exe.name + OLD_SUFFIX)


# ------------------------------------------------------------------ download

def download(release: Release, exe: Path, *, opener: Opener = urllib.request.urlopen,
             progress: Progress | None = None,
             cancelled: Callable[[], bool] = lambda: False,
             timeout: float = DOWNLOAD_TIMEOUT_S) -> Path:
    """The release's exe, next to ``exe``, verified. Deleted again on any failure."""
    if not release.asset_url:
        raise UpdateError(f"the release has no {ASSET_NAME}")
    if not release.sha256:
        raise UpdateError("the release gives no SHA-256 digest of the file")
    target = download_path(exe)
    digest = hashlib.sha256()
    received = 0
    try:
        with opener(_request(release.asset_url, "application/octet-stream"),
                    timeout=timeout) as response, target.open("wb") as file:
            expected = release.asset_size or int(response.headers.get("Content-Length") or 0)
            while True:
                if cancelled():
                    raise UpdateCancelled("cancelled")
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                file.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                if progress is not None:
                    progress(received, expected)
        if release.asset_size and received != release.asset_size:
            raise UpdateError(f"the download has {received} bytes, the release says "
                              f"{release.asset_size}")
        if digest.hexdigest() != release.sha256:
            raise UpdateError("the downloaded file does not match the release's SHA-256")
        with target.open("rb") as file:
            if file.read(2) != b"MZ":
                raise UpdateError("the downloaded file is not a Windows program")
    except BaseException as exc:
        with contextlib.suppress(OSError):
            target.unlink()
        if isinstance(exc, (urllib.error.URLError, OSError)) \
                and not isinstance(exc, UpdateError):
            raise UpdateError(str(getattr(exc, "reason", None) or exc)) from exc
        raise
    return target


# ------------------------------------------------------------------ starting a new exe

def child_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for another onefile exe: its own application, not our child.

    PyInstaller 6.9+ reads PYINSTALLER_RESET_ENVIRONMENT; the older variables are dropped
    too, so it holds whichever PyInstaller built the exe being started.
    """
    env = dict(os.environ if base is None else base)
    for key in list(env):
        if key.startswith("_PYI_") or key in ("_MEIPASS2", "_PYI_ONEDIR_MODE"):
            del env[key]
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def _creation_flags() -> int:
    if sys.platform != "win32":
        return 0
    return subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP


def self_check(exe: Path, *, run: Callable[..., Any] = subprocess.run,
               timeout: float = SELF_CHECK_TIMEOUT_S) -> None:
    """Start ``exe`` once to see that it comes up on this machine."""
    try:
        result = run([str(exe), SELF_CHECK_ARG], env=child_environment(), cwd=str(exe.parent),
                     timeout=timeout, capture_output=True, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateError(f"the new version does not start: {exc}") from exc
    if result.returncode != 0:
        raise UpdateError(f"the new version does not start (exit code {result.returncode})")


def start_detached(command: Sequence[str], *, cwd: Path | None = None,
                   popen: Callable[..., Any] = subprocess.Popen) -> None:
    popen(list(command), env=child_environment(), cwd=str(cwd) if cwd else None,
          close_fds=True, creationflags=_creation_flags(),
          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def restart_command(argv: Sequence[str] | None = None) -> list[str]:
    """How to start this application again, as it was started — minus ``--connect``: the
    new window must not log in by itself just because the old one did."""
    args = [a for a in (sys.argv[1:] if argv is None else argv) if a != "--connect"]
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "mqs", *args]


# ------------------------------------------------------------------ the swap

def _replace(source: Path, target: Path, *, sleep: Callable[[float], None] = time.sleep) -> None:
    for attempt in range(RENAME_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == RENAME_ATTEMPTS - 1:
                raise
            sleep(RENAME_PAUSE_S)


def install(downloaded: Path, exe: Path, *,
            sleep: Callable[[float], None] = time.sleep) -> Path:
    """Put ``downloaded`` in place of ``exe``; the old exe is kept beside it. Returns where.

    The exe is never left missing: when the new file cannot take its name, the old one
    gets it back.
    """
    aside = old_path(exe)
    with contextlib.suppress(FileNotFoundError):
        try:
            aside.unlink()
        except PermissionError:
            # The previous update's old exe is still running (or scanned): keep it, and
            # put this one aside under a name of its own.
            aside = exe.with_name(f"{exe.name}{OLD_SUFFIX}-{os.getpid()}")
    try:
        _replace(exe, aside, sleep=sleep)
    except OSError as exc:
        raise UpdateError(f"{exe.name} cannot be moved aside: {exc}") from exc
    try:
        _replace(downloaded, exe, sleep=sleep)
    except OSError as exc:
        try:
            _replace(aside, exe, sleep=sleep)
        except OSError:
            log.exception("the old exe could not be put back from %s", aside)
            raise UpdateError(f"{exe.name} could not be replaced and the old one could not "
                              f"be put back; it is in {aside}: {exc}") from exc
        raise UpdateError(f"{exe.name} could not be replaced: {exc}") from exc
    log.info("updated %s, previous version kept as %s", exe, aside)
    return aside


def cleanup(exe: Path, *, old: bool = True, download: bool = True) -> list[Path]:
    """Delete what earlier updates left next to ``exe``: old exes and broken downloads.
    What is still in use stays for the next start. Returns what was deleted.

    The two are separate because they are safe at different times. A broken download only
    before this run can have started one of its own — a start therefore takes it at once;
    an old exe only once the previous version has finished closing, so a while later.
    """
    removed: list[Path] = []
    candidates = [download_path(exe)] if download else []
    if old:
        candidates += exe.parent.glob(exe.name + OLD_SUFFIX + "*")
    for path in candidates:
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            log.info("leftover %s not deleted yet: %s", path, exc)
            continue
        removed.append(path)
    return removed
