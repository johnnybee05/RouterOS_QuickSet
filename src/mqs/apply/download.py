"""Fetching RouterOS packages from MikroTik, and keeping them.

`/system package update` downloads a whole set by itself and is the safe way to update —
but it only ever installs the newest release of a channel, so a named target version means
uploading the files, and that means having them. This module gets them.

Nothing that arrives is trusted on its name. A downloaded file is accepted only after
`read_npk` has read its header and the header says the package, the version and the
architecture that were asked for; otherwise it is deleted and the caller is told. The name
MikroTik gives a file (`apply/packages.py:npk_file_name`) is used to *ask*, never to
decide what a file is — which is also what makes a half-finished download or a 404 page
saved under the right name harmless.

The cache is flat, under the application data directory, and holds files exactly as
MikroTik publishes them, so `scan_update_dir` reads it like the Update directory next to
the exe. The Update directory wins where both have a version: a file the user put there is
the one they meant.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from mqs.apply.packages import (
    NpkError,
    NpkPackage,
    UpdateDir,
    is_main_package,
    npk_file_name,
    npk_url,
    read_npk,
    scan_update_dir,
)
from mqs.issues import Issue, Severity
from mqs.model.version import RosVersion

CACHE_DIR_NAME = "packages"
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_RETRIES = 2
CHUNK = 256 * 1024
# How far up a minor line to look for the newest published patch. The stop a route makes
# is "the newest 7.12.x", and MikroTik publishes no list of what exists, so it is asked
# for upward until two in a row are missing.
PATCH_LIMIT = 15
PATCH_MISSES = 2


class Opener(Protocol):
    """`urllib.request.urlopen`, narrowed to what is used here and injectable in tests."""

    def __call__(self, request: urllib.request.Request, timeout: float) -> Any: ...


def _issue(code: str, severity: Severity = Severity.ERROR, **params: object) -> Issue:
    return Issue(code, severity, "", dict(params))


class DownloadFailed(Exception):
    """One package did not arrive, or arrived as something else. ``issue`` says which."""

    def __init__(self, issue: Issue) -> None:
        super().__init__(issue.message)
        self.issue = issue


@dataclass(frozen=True)
class Fetched:
    package: NpkPackage
    cached: bool  # already here, nothing downloaded


Progress = Callable[[str, int, int], None]  # file name, bytes so far, bytes expected (0 = ?)


@dataclass
class PackageCache:
    """Where downloaded packages live, and how they get there."""

    directory: Path
    opener: Opener = urllib.request.urlopen
    timeout: float = DEFAULT_TIMEOUT_S
    retries: int = DEFAULT_RETRIES
    on_progress: Progress = lambda file, done, total: None
    # Versions already asked about upstream, so a route planned twice costs one round of
    # requests. Keyed by (architecture, major, minor).
    _patches: dict[tuple[str, int, int], RosVersion | None] = field(default_factory=dict)

    def scan(self) -> UpdateDir:
        return scan_update_dir(self.directory)

    def path_of(self, package: str, version: RosVersion, device_arch: str) -> Path:
        return self.directory / npk_file_name(package, version, device_arch)

    def have(self, package: str, version: RosVersion, device_arch: str,
             header_arch: str) -> NpkPackage | None:
        """A cached file that really is the package asked for, or None.

        A file left half-written by an interrupted run has a truncated header and fails to
        read, which is exactly what should happen to it.
        """
        path = self.path_of(package, version, device_arch)
        if not path.is_file():
            return None
        try:
            found = read_npk(path)
        except (OSError, NpkError, ValueError):
            return None
        return found if _matches(found, package, version, header_arch) else None

    def fetch(self, package: str, version: RosVersion, device_arch: str,
              header_arch: str) -> Fetched:
        """The package, from the cache or from MikroTik. Raises `DownloadFailed`."""
        cached = self.have(package, version, device_arch, header_arch)
        if cached is not None:
            return Fetched(cached, cached=True)

        url = npk_url(package, version, device_arch)
        path = self.path_of(package, version, device_arch)
        partial = path.with_suffix(path.suffix + ".part")
        self.directory.mkdir(parents=True, exist_ok=True)
        last: Issue | None = None
        for attempt in range(self.retries + 1):
            try:
                self._download(url, partial, path.name)
            except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError) as exc:
                last = _transfer_issue(exc, package, version, url)
                partial.unlink(missing_ok=True)
                if _final(exc) or attempt == self.retries:
                    break
                continue
            try:
                found = read_npk(partial)
            except (OSError, NpkError, ValueError) as exc:
                partial.unlink(missing_ok=True)
                raise DownloadFailed(_issue("download.not_a_package", file=path.name,
                                            url=url, error=str(exc))) from exc
            if not _matches(found, package, version, header_arch):
                partial.unlink(missing_ok=True)
                raise DownloadFailed(_issue(
                    "download.wrong_package", file=path.name, url=url,
                    name=found.display_name, version=str(found.version),
                    arch=found.architecture, want_name=package,
                    want_version=str(version), want_arch=header_arch))
            partial.replace(path)
            return Fetched(read_npk(path), cached=False)
        raise DownloadFailed(last or _issue("download.failed", name=package,
                                            version=str(version), url=url, error="?"))

    def _download(self, url: str, into: Path, label: str) -> None:
        request = urllib.request.Request(url, method="GET")
        with self.opener(request, self.timeout) as response:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            self.on_progress(label, 0, total)
            with into.open("wb") as handle:
                while True:
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    self.on_progress(label, done, total)

    def exists(self, url: str) -> bool:
        """Whether MikroTik publishes this file, asked without downloading it."""
        request = urllib.request.Request(url, method="HEAD")
        try:
            with self.opener(request, self.timeout) as response:
                return int(getattr(response, "status", 200) or 200) < 400
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError):
            return False

    def newest_patch(self, base: RosVersion, device_arch: str) -> RosVersion | None:
        """The newest published patch of ``base``'s minor line — "the newest 7.12.x".

        MikroTik publishes no index, so the line is walked upward from `7.12` and stopped
        after two misses in a row; a pulled release in the middle therefore does not hide
        the ones above it. The answer is remembered for the session.
        """
        key = (device_arch, base.major, base.minor)
        if key in self._patches:
            return self._patches[key]
        found: RosVersion | None = None
        misses = 0
        for patch in range(PATCH_LIMIT + 1):
            candidate = RosVersion(major=base.major, minor=base.minor, patch=patch)
            if self.exists(npk_url("routeros", candidate, device_arch)):
                found, misses = candidate, 0
            else:
                misses += 1
                if misses >= PATCH_MISSES:
                    break
        self._patches[key] = found
        return found


def _matches(found: NpkPackage, package: str, version: RosVersion, header_arch: str) -> bool:
    """The header says what was asked for.

    The main package goes by three names — `routeros` in a URL, `system` in a v7 header,
    `routeros-<arch>` in a v6 one — so it is compared by kind rather than by string.
    """
    named = (is_main_package(found.name) if is_main_package(package)
             else found.name == package)
    return named and found.version == version and found.architecture == header_arch


def _final(exc: BaseException) -> bool:
    """Whether retrying is pointless: the server answered, and the answer was no."""
    return isinstance(exc, urllib.error.HTTPError) and 400 <= exc.code < 500


def _transfer_issue(exc: BaseException, package: str, version: RosVersion,
                    url: str) -> Issue:
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
        return _issue("download.not_published", name=package, version=str(version), url=url)
    return _issue("download.failed", name=package, version=str(version), url=url,
                  error=str(exc))


# ------------------------------------------------------------------ the two sources together


@dataclass
class PackageSource:
    """The Update directory next to the exe, the cache, and MikroTik — in that order.

    ``download`` off is the offline mode: the two directories are all there is, and a
    package that is in neither is simply missing. Nothing here reaches the network unless
    it is on.
    """

    offline: Path
    cache: PackageCache
    download: bool = True

    def available(self) -> UpdateDir:
        """Everything on this PC, the Update directory winning where both have a version."""
        found = scan_update_dir(self.offline)
        if not found.exists:
            found = UpdateDir(self.offline, exists=self.cache.directory.is_dir())
        return found.with_packages(self.cache.scan().packages)

    def obtain(self, names: Iterable[str], version: RosVersion, device_arch: str,
               header_arch: str) -> tuple[UpdateDir, list[Issue]]:
        """Make sure ``names`` are on this PC in ``version``, then say what is here.

        Every package is tried, so one that is not published is reported alongside the
        others rather than hiding them: the user wants the whole list of what is wrong
        with a target version, not the first item of it.
        """
        issues: list[Issue] = []
        if self.download:
            for name in sorted(names):
                try:
                    self.cache.fetch(name, version, device_arch, header_arch)
                except DownloadFailed as exc:
                    issues.append(exc.issue)
        return self.available(), issues

    def stops(self, versions: Sequence[RosVersion], device_arch: str) -> list[RosVersion]:
        """The newest published patch of each minor line, for the stops a route makes.

        Offline this is answered from the files on the PC, which is the honest answer
        there: a stop that is not on this PC cannot be made.
        """
        if not self.download:
            return []
        found = [self.cache.newest_patch(v, device_arch) for v in versions]
        return [v for v in found if v is not None]


def cache_dir(app_data: Path) -> Path:
    return app_data / CACHE_DIR_NAME
