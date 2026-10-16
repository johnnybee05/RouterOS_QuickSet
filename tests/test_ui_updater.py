"""The window's update flow and language choice, with fakes for everything that reaches out."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from mqs import i18n
from mqs.selfupdate import Release, UpdateError
from mqs.ui import strings_cs as S
from mqs.ui.dialogs import UpdateDialog
from mqs.ui.updater import INSTALL, LATER, OPEN_PAGE, SKIP, AppUpdater

pytestmark = pytest.mark.qt

RELEASE = Release("0.13.0", "## Přidáno\n\n- jazyky", "https://example.invalid/page",
                  "https://example.invalid/exe", 10, "ab" * 32)


class FakeProgress(QObject):
    canceled = Signal()

    def __init__(self):
        super().__init__()
        self.values = []
        self.closed = False

    def setMaximum(self, value):
        pass

    def setValue(self, value):
        self.values.append(value)

    def close(self):
        self.closed = True


class FakeDialogs:
    def __init__(self, choice=INSTALL):
        self.choice = choice
        self.errors: list[str] = []
        self.infos: list[str] = []
        self.offered: list[tuple[Release, str, str]] = []
        self.urls: list[str] = []
        self.progress = FakeProgress()

    def error(self, parent, text):
        self.errors.append(text)

    def info(self, parent, text):
        self.infos.append(text)

    def ask_update(self, parent, release, current, blocker):
        self.offered.append((release, current, blocker))
        return self.choice

    def download_progress(self, parent, title, text):
        return self.progress

    def open_url(self, url):
        self.urls.append(url)


class Harness:
    def __init__(self, qtbot, tmp_path: Path, *, choice=INSTALL, release=RELEASE, busy=False,
                 frozen=True, check_error=None, install_error=None, skipped=""):
        self.exe = tmp_path / "MikrotikQuickSetPlus.exe"
        self.exe.write_bytes(b"MZ old")
        self.dialogs = FakeDialogs(choice)
        self.restarts: list[list[str]] = []
        self.skipped = skipped
        self.installed: list[tuple[Path, Path]] = []
        self.checked: list[Path] = []
        self.busy = busy
        self.widget = QWidget()
        qtbot.addWidget(self.widget)

        def check():
            if check_error:
                raise check_error
            return release

        def download(rel, exe, *, progress, cancelled):
            progress(5, 10)
            progress(10, 10)
            path = exe.with_name("MikrotikQuickSetPlus.update.exe")
            path.write_bytes(b"MZ new")
            return path

        def install(downloaded, exe):
            if install_error:
                raise install_error
            self.installed.append((downloaded, exe))
            return exe.with_name(exe.name + ".old")

        self.updater = AppUpdater(
            self.widget, self.dialogs, busy=lambda: self.busy,
            restart=self.restarts.append, skipped=lambda: self.skipped,
            skip=lambda version: setattr(self, "skipped", version),
            check_fn=check, download_fn=download, self_check_fn=self.checked.append,
            install_fn=install, exe_fn=lambda: self.exe if frozen else None,
            writable_fn=lambda directory: True, current="0.12.1",
        )

    def check(self, qtbot, manual):
        self.updater.check(manual=manual)
        qtbot.waitUntil(lambda: not self.updater.checking and not self.updater.downloading,
                        timeout=5000)


def test_a_newer_release_is_downloaded_checked_installed_and_restarted(qtbot, tmp_path):
    h = Harness(qtbot, tmp_path)
    h.check(qtbot, manual=False)
    release, current, blocker = h.dialogs.offered[0]
    assert (release.version, current, blocker) == ("0.13.0", "0.12.1", "")
    qtbot.waitUntil(lambda: bool(h.restarts), timeout=5000)
    downloaded = h.exe.with_name("MikrotikQuickSetPlus.update.exe")
    assert h.checked == [downloaded]  # the new exe ran before anything was replaced
    assert h.installed == [(downloaded, h.exe)]
    assert h.restarts[0][0] == str(h.exe)
    assert h.dialogs.progress.closed and h.dialogs.progress.values
    assert h.dialogs.errors == []


def test_nothing_is_replaced_while_something_runs(qtbot, tmp_path):
    h = Harness(qtbot, tmp_path, busy=True)
    h.check(qtbot, manual=False)
    assert h.dialogs.errors == [S.UPDATE_BUSY]
    assert h.installed == [] and h.restarts == []


def test_a_new_exe_that_fails_its_check_is_not_installed(qtbot, tmp_path):
    h = Harness(qtbot, tmp_path)

    def refuse(path):
        raise UpdateError("the new version does not start (exit code 1)")

    h.updater.self_check_fn = refuse
    h.check(qtbot, manual=False)
    qtbot.waitUntil(lambda: bool(h.dialogs.errors), timeout=5000)
    assert "does not start" in h.dialogs.errors[0]
    assert h.installed == [] and h.restarts == []
    assert not h.exe.with_name("MikrotikQuickSetPlus.update.exe").exists()


def test_a_failed_swap_keeps_the_running_version(qtbot, tmp_path):
    h = Harness(qtbot, tmp_path, install_error=UpdateError("locked"))
    h.check(qtbot, manual=False)
    qtbot.waitUntil(lambda: bool(h.dialogs.errors), timeout=5000)
    assert h.dialogs.errors == [S.UPDATE_INSTALL_FAILED.format(error="locked")]
    assert h.restarts == []


def test_skip_open_page_and_later(qtbot, tmp_path):
    h = Harness(qtbot, tmp_path, choice=SKIP)
    h.check(qtbot, manual=False)
    assert h.skipped == "0.13.0"
    h.dialogs.offered.clear()
    h.check(qtbot, manual=False)
    assert h.dialogs.offered == []  # skipped: not offered on start again …
    h.check(qtbot, manual=True)
    assert len(h.dialogs.offered) == 1  # … but asked for, it is
    h.dialogs.choice = OPEN_PAGE
    h.check(qtbot, manual=True)
    assert h.dialogs.urls == [RELEASE.page_url]
    h.dialogs.choice = LATER
    h.check(qtbot, manual=True)
    assert h.installed == [] and h.restarts == []


def test_up_to_date_and_failures_speak_only_when_asked(qtbot, tmp_path):
    same = Release("0.12.1", "", "page")
    h = Harness(qtbot, tmp_path, release=same)
    h.check(qtbot, manual=False)
    assert h.dialogs.infos == [] and h.dialogs.offered == []
    h.check(qtbot, manual=True)
    assert h.dialogs.infos == [S.UPDATE_UP_TO_DATE.format(version="0.12.1")]

    failing = Harness(qtbot, tmp_path, check_error=UpdateError("offline"))
    failing.check(qtbot, manual=False)
    assert failing.dialogs.errors == []
    failing.check(qtbot, manual=True)
    assert failing.dialogs.errors == [S.UPDATE_CHECK_FAILED.format(error="offline")]


def test_from_the_sources_or_without_a_digest_only_the_page_is_offered(qtbot, tmp_path):
    h = Harness(qtbot, tmp_path, frozen=False)
    assert h.updater.blocker(RELEASE) == S.UPDATE_NOT_FROZEN
    h = Harness(qtbot, tmp_path)
    assert h.updater.blocker(Release("0.13.0", "", "page", "url", 10, "")) == S.UPDATE_NO_DIGEST
    h.updater.writable_fn = lambda directory: False
    assert h.updater.blocker(RELEASE).startswith(S.UPDATE_NOT_WRITABLE.split("(")[0])


def test_update_dialog_cannot_install_what_it_cannot_verify(qtbot):
    dialog = UpdateDialog(None, RELEASE, "0.12.1", S.UPDATE_NO_DIGEST)
    qtbot.addWidget(dialog)
    assert not dialog.buttons["install"].isEnabled()
    dialog.buttons["skip"].click()
    assert dialog.choice == SKIP
    dialog = UpdateDialog(None, RELEASE, "0.12.1", "")
    qtbot.addWidget(dialog)
    assert dialog.buttons["install"].isEnabled()
    assert dialog.choice == LATER  # closing it is "later"


def test_the_window_builds_in_english(qtbot, tmp_path):
    from mqs.profiles.store import ProfileStore
    from mqs.ui.main_window import MainWindow
    from mqs.ui.settings import SettingsStore

    i18n.set_language("en")
    window = MainWindow(
        store=ProfileStore(tmp_path / "profiles"),
        settings_store=SettingsStore(tmp_path / "s", protect=bytes, unprotect=bytes),
        network_kwargs={"inventory_path": tmp_path / "inventory.json",
                        "update_dir": tmp_path / "Update", "backup_dir": tmp_path / "b"},
        apply_kwargs={"update_dir": tmp_path / "Update"})
    qtbot.addWidget(window)
    menus = [action.text() for action in window.menuBar().actions()]
    assert menus == ["Profile", "View", "Help"]
    assert window.nav.item(0).text() == "Device"
