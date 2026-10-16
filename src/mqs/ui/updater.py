"""The window's side of updating itself: ask, download with progress, swap, restart.

What may be replaced and how is mqs.selfupdate; this decides when. The network work runs
on daemon threads rather than the window's TaskRunner: a check that hangs on a slow
network must neither hold the window open at close nor count as "something is running"
when the user wants to install.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QWidget

from mqs import __version__, selfupdate
from mqs.selfupdate import Release, UpdateCancelled, UpdateError
from mqs.ui import strings as S
from mqs.ui.dialogs import Dialogs

log = logging.getLogger(__name__)

INSTALL = "install"
OPEN_PAGE = "page"
SKIP = "skip"
LATER = "later"


class _Bridge(QObject):
    """Lives in the UI thread; carries a callback and its value over from a worker thread."""

    deliver = Signal(object, object)

    def __init__(self) -> None:
        super().__init__()
        self.deliver.connect(self._run)

    @Slot(object, object)
    def _run(self, callback: Callable[[Any], None], value: object) -> None:
        callback(value)


class AppUpdater(QObject):
    def __init__(
        self,
        window: QWidget,
        dialogs: Dialogs,
        *,
        busy: Callable[[], bool],
        restart: Callable[[list[str]], None],
        skipped: Callable[[], str],
        skip: Callable[[str], None],
        check_fn: Callable[[], Release] = selfupdate.latest_release,
        download_fn: Callable[..., Path] = selfupdate.download,
        self_check_fn: Callable[[Path], None] = selfupdate.self_check,
        install_fn: Callable[[Path, Path], Path] = selfupdate.install,
        exe_fn: Callable[[], Path | None] = selfupdate.running_exe,
        writable_fn: Callable[[Path], bool] = selfupdate.directory_writable,
        current: str = __version__,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.dialogs = dialogs
        self.busy = busy
        self.restart = restart
        self.skipped = skipped
        self.skip = skip
        self.check_fn = check_fn
        self.download_fn = download_fn
        self.self_check_fn = self_check_fn
        self.install_fn = install_fn
        self.exe_fn = exe_fn
        self.writable_fn = writable_fn
        self.current = current
        self.checking = False
        self.downloading = False
        self._bridge = _Bridge()

    # ------------------------------------------------------------ background work

    def _background(self, work: Callable[[Callable[[object], None]], object],
                    on_done: Callable[[Any], None], on_error: Callable[[BaseException], None],
                    on_progress: Callable[[Any], None] = lambda value: None) -> None:
        def progress(value: object) -> None:
            self._bridge.deliver.emit(on_progress, value)

        def run() -> None:
            try:
                result = work(progress)
            except BaseException as exc:
                self._bridge.deliver.emit(on_error, exc)
            else:
                self._bridge.deliver.emit(on_done, result)

        threading.Thread(target=run, name="mqs-update", daemon=True).start()

    # ------------------------------------------------------------ check

    def check(self, *, manual: bool) -> None:
        """Ask GitHub for the newest release. On start (not manual) a failure, no news or a
        skipped version say nothing; asked for, every outcome is reported."""
        if self.checking or self.downloading:
            return
        self.checking = True
        self._background(lambda progress: self.check_fn(),
                         lambda release: self._checked(release, manual),
                         lambda exc: self._check_failed(exc, manual))

    def _check_failed(self, exc: BaseException, manual: bool) -> None:
        self.checking = False
        log.info("update check failed: %s", exc)
        if manual:
            self.dialogs.error(self.window, S.UPDATE_CHECK_FAILED.format(error=exc))

    def _checked(self, release: Release, manual: bool) -> None:
        self.checking = False
        if not selfupdate.is_newer(release.version, self.current):
            log.info("no update: newest release %s, running %s", release.version, self.current)
            if manual:
                self.dialogs.info(self.window, S.UPDATE_UP_TO_DATE.format(version=self.current))
            return
        if not manual and release.version == self.skipped():
            log.info("update %s skipped by the user", release.version)
            return
        self.offer(release)

    def blocker(self, release: Release) -> str:
        """Why this release cannot be installed in place, or "" when it can."""
        exe = self.exe_fn()
        if exe is None:
            return S.UPDATE_NOT_FROZEN
        if not release.asset_url or not release.sha256:
            return S.UPDATE_NO_DIGEST
        if not self.writable_fn(exe.parent):
            return S.UPDATE_NOT_WRITABLE.format(dir=exe.parent)
        return ""

    def offer(self, release: Release) -> None:
        choice = self.dialogs.ask_update(self.window, release, self.current,
                                         self.blocker(release))
        if choice == OPEN_PAGE:
            self.dialogs.open_url(release.page_url)
        elif choice == SKIP:
            self.skip(release.version)
        elif choice == INSTALL:
            self.install(release)

    # ------------------------------------------------------------ download and install

    def install(self, release: Release) -> None:
        exe = self.exe_fn()
        if exe is None or self.blocker(release):
            return
        if self.busy():
            self.dialogs.error(self.window, S.UPDATE_BUSY)
            return
        self.downloading = True
        progress = self.dialogs.download_progress(
            self.window, S.UPDATE_TITLE, S.UPDATE_DOWNLOADING.format(version=release.version))
        cancel = threading.Event()
        progress.canceled.connect(cancel.set)

        def work(report: Callable[[object], None]) -> Path:
            path = self.download_fn(release, exe,
                                    progress=lambda done, total: report((done, total)),
                                    cancelled=cancel.is_set)
            try:
                self.self_check_fn(path)
            except BaseException:
                with contextlib.suppress(OSError):
                    path.unlink()
                raise
            return path

        def on_progress(value: tuple[int, int]) -> None:
            done, total = value
            if total:
                progress.setMaximum(1000)
                progress.setValue(min(999, done * 1000 // total))

        def on_error(exc: BaseException) -> None:
            self.downloading = False
            progress.close()
            if isinstance(exc, UpdateCancelled):
                return
            log.warning("update %s not downloaded: %s", release.version, exc)
            self.dialogs.error(self.window, S.UPDATE_DOWNLOAD_FAILED.format(error=exc))

        def on_done(path: Path) -> None:
            self.downloading = False
            progress.close()
            self._swap(release, path, exe)

        self._background(work, on_done, on_error, on_progress)

    def _swap(self, release: Release, downloaded: Path, exe: Path) -> None:
        if self.busy():  # started while the download ran
            with contextlib.suppress(OSError):
                downloaded.unlink()
            self.dialogs.error(self.window, S.UPDATE_BUSY)
            return
        self.dialogs.info(self.window, S.UPDATE_READY.format(version=release.version))
        try:
            self.install_fn(downloaded, exe)
        except UpdateError as exc:
            with contextlib.suppress(OSError):
                downloaded.unlink()
            log.warning("update %s not installed: %s", release.version, exc)
            self.dialogs.error(self.window, S.UPDATE_INSTALL_FAILED.format(error=exc))
            return
        self.restart([str(exe), *selfupdate.restart_command()[1:]])
