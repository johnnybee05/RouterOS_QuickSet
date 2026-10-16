"""Main window: navigation on the left, pages on the right, dialect header and status bar."""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from PySide6.QtCore import QLocale, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mqs import __version__, i18n, selfupdate
from mqs.apply.session import DeviceSession, end_session
from mqs.logging_setup import SecretRegistry
from mqs.messages import format_message
from mqs.model.presets import apply_preset
from mqs.model.profile import DeviceProfile, Mode
from mqs.paths import app_data_dir, profiles_dir
from mqs.profiles.store import ProfileError, ProfileStore, export_profile, import_profile
from mqs.ui import strings as S
from mqs.ui.controller import Analysis, ProfileController
from mqs.ui.dialogs import Dialogs
from mqs.ui.pages.apply import ApplyPage
from mqs.ui.pages.base import SECRET_PROPERTY, show_secret
from mqs.ui.pages.basic import BasicPage
from mqs.ui.pages.device import DevicePage, connection_summary
from mqs.ui.pages.firewall import FirewallPage
from mqs.ui.pages.lan_bridge import LanBridgePage
from mqs.ui.pages.network import NetworkPage
from mqs.ui.pages.preview import PreviewPage
from mqs.ui.pages.services import ServicesPage
from mqs.ui.settings import SettingsStore
from mqs.ui.updater import AppUpdater
from mqs.ui.workers import TaskRunner

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        registry: SecretRegistry | None = None,
        dark: bool = False,
        dialogs: Dialogs | None = None,
        store: ProfileStore | None = None,
        settings_store: SettingsStore | None = None,
        device_kwargs: dict[str, Any] | None = None,
        apply_kwargs: dict[str, Any] | None = None,
        network_kwargs: dict[str, Any] | None = None,
        updater_kwargs: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(f"{S.APP_TITLE} {__version__}")
        self.resize(1280, 860)
        self.dialogs = dialogs or Dialogs()
        self.store = store or ProfileStore(profiles_dir())
        self.registry = registry or SecretRegistry()
        self.settings_store = settings_store or SettingsStore(app_data_dir())
        self.settings = self.settings_store.load_settings()
        self.runner = TaskRunner(self)
        self.controller = ProfileController(self)
        # Set when the window closes to start again (a new language, an installed update):
        # mqs.app.main starts it once the window is gone.
        self.restart_command: list[str] = []
        self.updater = AppUpdater(self, self.dialogs, busy=lambda: self.runner.busy,
                                  restart=self.request_restart,
                                  skipped=lambda: self.settings.skipped_version,
                                  skip=self.skip_version, **(updater_kwargs or {}))

        self.device_page = DevicePage(self.controller, self.runner, self.dialogs, self.registry,
                                      **(device_kwargs or {}))
        # The Hromadný update page logs in with whatever the Zařízení page's form holds: one set of
        # credentials in the window, not two that drift apart. What a device took instead is
        # remembered by the page itself, per device.
        self.network_page = NetworkPage(self.controller, self.runner, self.dialogs, **{
            "login_settings": self.device_page.login_settings, **(network_kwargs or {})})
        self.basic_page = BasicPage(self.controller)
        self.lan_page = LanBridgePage(self.controller)
        self.firewall_page = FirewallPage(
            self.controller,
            ask_shaping=lambda parent: self.dialogs.question(
                parent, S.FASTTRACK_SHAPING_TITLE, S.FASTTRACK_SHAPING_QUESTION))
        self.services_page = ServicesPage(
            self.controller, dark=dark,
            confirm=lambda parent: self.dialogs.question(parent, S.WARNING_TITLE,
                                                         S.SERVICES_RESET_CONFIRM))
        self.preview_page = PreviewPage(
            self.controller, dark=dark,
            save_dialog=lambda parent, suggested: self.dialogs.save_file(
                parent, S.PREVIEW_SAVE, suggested, S.PREVIEW_SAVE_FILTER))
        self.apply_page = ApplyPage(self.controller, self.runner, self.dialogs,
                                    lambda: self.device_page.session,
                                    save_script=self.preview_page.save_script,
                                    **(apply_kwargs or {}))
        self.pages = {
            "device": self.device_page, "network": self.network_page,
            "basic": self.basic_page, "lan": self.lan_page,
            "firewall": self.firewall_page, "services": self.services_page,
            "preview": self.preview_page, "apply": self.apply_page,
        }

        central = QWidget()
        outer = QVBoxLayout(central)
        header = QHBoxLayout()
        title = QLabel(S.APP_TITLE)
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.dialect_badge = QLabel()
        self.dialect_badge.setProperty("role", "badge")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.dialect_badge)
        outer.addLayout(header)
        body = QHBoxLayout()
        self.nav = QListWidget()
        self.nav.setMaximumWidth(200)
        self.stack = QStackedWidget()
        for key, page in self.pages.items():
            self.nav.addItem(S.NAV[key])
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)
        self.setCentralWidget(central)

        self.status_connection = QLabel(S.DISCONNECTED)
        self.status_device = QLabel()
        self.status_security = QLabel()
        for label in (self.status_connection, self.status_device, self.status_security):
            self.statusBar().addPermanentWidget(label)

        self._build_menu()
        self._restore_settings()
        self.device_page.sessionChanged.connect(self._session_changed)
        self.apply_page.applied.connect(self.device_page.adopt_apply)
        self.apply_page.version_changed.connect(self.device_page.adopt_version_change)
        # After adopt_apply: it is the connection the apply ended on that gets closed, and
        # the address lent on it that gets given back.
        self.apply_page.finished_with_device.connect(self.device_page.disconnect_device)
        self.controller.analysisReady.connect(self._analysis_ready)
        self.controller.inventoryChanged.connect(lambda _: self._update_badge())

        self.current_profile_name = ""
        self.new_profile()
        self.controller.recompute()
        self.nav.setCurrentRow(0)

    # ------------------------------------------------------------ menu / profiles

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu(S.MENU_PROFILE)
        actions = [
            (S.PROFILE_NEW, self.new_profile),
            (S.PROFILE_OPEN, self.open_profile),
            (S.PROFILE_SAVE, self.save_profile),
            (S.PROFILE_SAVE_AS, self.save_profile_as),
            (S.PROFILE_IMPORT, self.import_profile_file),
            (S.PROFILE_EXPORT, self.export_profile_file),
        ]
        self.profile_actions: dict[str, QAction] = {}
        for label, slot in actions:
            action = menu.addAction(label)
            action.triggered.connect(slot)
            self.profile_actions[label] = action

        view = self.menuBar().addMenu(S.MENU_VIEW)
        self.show_passwords_action = view.addAction(S.VIEW_SHOW_PASSWORDS)
        self.show_passwords_action.setCheckable(True)
        self.show_passwords_action.toggled.connect(self.set_show_passwords)
        language_menu = view.addMenu(S.VIEW_LANGUAGE)
        self.language_actions: dict[str, QAction] = {}
        group = QActionGroup(self)
        for code, label in ((i18n.AUTO, S.LANGUAGE_AUTO), *i18n.LANGUAGES.items()):
            action = language_menu.addAction(label)
            action.setCheckable(True)
            group.addAction(action)
            action.triggered.connect(lambda _=False, c=code: self.choose_language(c))
            self.language_actions[code] = action

        help_menu = self.menuBar().addMenu(S.MENU_HELP)
        help_menu.addAction(S.UPDATE_CHECK).triggered.connect(
            lambda: self.updater.check(manual=True))
        self.check_updates_action = help_menu.addAction(S.UPDATE_AUTO)
        self.check_updates_action.setCheckable(True)
        self.check_updates_action.toggled.connect(self.set_check_updates)
        help_menu.addSeparator()
        help_menu.addAction(S.UPDATE_ABOUT).triggered.connect(self.show_about)

    # ------------------------------------------------------------ remembered settings

    def _restore_settings(self) -> None:
        page = self.device_page
        remember = self.settings.remember_login and self.settings_store.can_remember_login
        if remember:
            login = self.settings_store.load_login()
            if login is not None:
                page.restore_login(login)
        page.remember_login.setEnabled(self.settings_store.can_remember_login)
        page.remember_login.setChecked(remember)
        page.remember_login.toggled.connect(self.set_remember_login)
        # Once a login has worked, not only at the close: a crash must not take it along.
        page.sessionChanged.connect(lambda session: self._save_login() if session else None)
        self.show_passwords_action.setChecked(self.settings.show_passwords)
        self.set_show_passwords(self.settings.show_passwords)
        chosen = self.settings.language if self.settings.language in i18n.LANGUAGES else i18n.AUTO
        self.language_actions[chosen].setChecked(True)
        self.check_updates_action.setChecked(self.settings.check_updates)

    def set_show_passwords(self, show: bool) -> None:
        for field in self.findChildren(QLineEdit):
            if field.property(SECRET_PROPERTY):
                show_secret(field, show)
        if show != self.settings.show_passwords:
            self.settings.show_passwords = show
            self._save_settings()

    def set_remember_login(self, remember: bool) -> None:
        self.settings.remember_login = remember
        self._save_settings()
        if remember:
            self._save_login()
        else:
            self.settings_store.forget_login()  # unticked: nothing of it stays on disk

    def choose_language(self, code: str) -> None:
        """Remember the language; it takes a restart, offered when it changes anything."""
        if code == self.settings.language:
            return
        self.settings.language = code
        self._save_settings()
        if i18n.resolve(code, system=QLocale.system().name()) == i18n.language():
            return
        if self.runner.busy:
            self.dialogs.info(self, S.LANGUAGE_RESTART_BUSY)
        elif self.dialogs.question(self, S.LANGUAGE_RESTART_TITLE, S.LANGUAGE_RESTART):
            self.request_restart(selfupdate.restart_command())

    def set_check_updates(self, check: bool) -> None:
        if check != self.settings.check_updates:
            self.settings.check_updates = check
            self._save_settings()

    def skip_version(self, version: str) -> None:
        self.settings.skipped_version = version
        self._save_settings()

    def start_update_check(self) -> None:
        """What a start does about updates: tidy up after the last one, look for the next."""
        exe = selfupdate.running_exe()
        if exe is not None:
            # A broken download now, before this run can start one of its own; the old exe
            # later, as it is still closing while the new one comes up.
            selfupdate.cleanup(exe, old=False)
            QTimer.singleShot(15000, lambda: selfupdate.cleanup(exe, download=False))
        if self.settings.check_updates:
            QTimer.singleShot(3000, lambda: self.updater.check(manual=False))

    def show_about(self) -> None:
        self.dialogs.info(self, S.UPDATE_ABOUT_TEXT.format(
            app=S.APP_TITLE, version=__version__, url=selfupdate.RELEASES_PAGE))

    def request_restart(self, command: list[str]) -> None:
        self._save_login()
        self._save_settings()
        self.restart_command = list(command)
        self.close()

    def _save_login(self) -> None:
        if self.settings.remember_login:
            try:
                self.settings_store.save_login(self.device_page.saved_login())
            except OSError as exc:
                log.warning("login form not saved: %s", exc)

    def _save_settings(self) -> None:
        try:
            self.settings_store.save_settings(self.settings)
        except OSError as exc:
            log.warning("settings not saved: %s", exc)

    def _profile_error(self, exc: ProfileError) -> None:
        self.dialogs.error(self, format_message(exc.code))
        log.warning("profile error: %s", exc)

    def new_profile(self) -> None:
        self.current_profile_name = ""
        profile = DeviceProfile()  # its ports by the names it has, not the old profile's
        self.controller.set_profile(
            apply_preset(profile, Mode.ROUTER, self.controller.ports(profile)))

    def open_profile(self) -> None:
        name = self.dialogs.ask_item(self, S.PROFILE_OPEN, S.PROFILE_NAME_PROMPT, self.store.list())
        if not name:
            return
        try:
            self.controller.set_profile(self.store.load(name))
        except ProfileError as exc:
            self._profile_error(exc)
            return
        self.current_profile_name = name

    def save_profile(self) -> None:
        if not self.current_profile_name:
            self.save_profile_as()
            return
        try:
            self.store.save(self.controller.profile, self.current_profile_name)
        except ProfileError as exc:
            self._profile_error(exc)

    def save_profile_as(self) -> None:
        name = self.dialogs.ask_text(self, S.PROFILE_SAVE_AS, S.PROFILE_NAME_PROMPT,
                                     self.controller.profile.name)
        if not name:
            return
        try:
            self.store.save(self.controller.profile, name)
        except ProfileError as exc:
            self._profile_error(exc)
            return
        self.current_profile_name = name
        self.controller.edit(lambda p: p.model_copy(update={"name": name}))

    def import_profile_file(self) -> None:
        path = self.dialogs.open_file(self, S.PROFILE_IMPORT, S.PROFILE_FILTER)
        if path is None:
            return
        try:
            self.controller.set_profile(import_profile(path))
        except ProfileError as exc:
            self._profile_error(exc)

    def export_profile_file(self) -> None:
        suggested = (self.controller.profile.name or "profil") + ".json"
        path = self.dialogs.save_file(self, S.PROFILE_EXPORT, suggested, S.PROFILE_FILTER)
        if path is not None:
            export_profile(self.controller.profile, path)

    # ------------------------------------------------------------ status

    def _session_changed(self, session: DeviceSession | None) -> None:
        if session is None:
            self.status_connection.setText(S.DISCONNECTED)
            self.status_device.setText("")
            self.status_security.setText("")
        else:
            inventory = session.inventory
            transport = session.transport
            self.status_connection.setText(connection_summary(session, with_port=True))
            self.status_device.setText(f"RouterOS {inventory.version} | "
                                       f"{inventory.resource.board_name}")
            fingerprint = getattr(transport, "host_key_fingerprint", "") or ""
            self.status_security.setText(S.SECURITY[transport.peer_security.value].format(
                fingerprint=fingerprint))
        self._update_badge()
        self.apply_page.refresh()

    def _update_badge(self) -> None:
        inventory = self.controller.inventory
        if inventory is not None:
            self.dialect_badge.setText(S.DIALECT_BADGE.format(major=inventory.version.major,
                                                              version=inventory.version))
        else:
            self.dialect_badge.setText(S.OFFLINE_DIALECT.format(
                version=self.controller.offline_version))

    def _analysis_ready(self, analysis: Analysis) -> None:
        self._update_badge()

    @property
    def has_syntax_errors(self) -> bool:
        return any(page.has_syntax_errors for page in self.pages.values())

    def set_dark(self, dark: bool) -> None:
        self.services_page.set_dark(dark)
        self.preview_page.set_dark(dark)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_login()  # the form as it is left is what the next start begins with
        self.device_page.shutdown()
        self.network_page.shutdown()
        self.runner.cancel_all()
        self.runner.wait_all(3000)
        session = self.device_page.session
        if session is not None:
            with contextlib.suppress(Exception):  # closing on exit must never raise
                end_session(session)  # gives a lent address back first
        self.registry.clear()
        super().closeEvent(event)
