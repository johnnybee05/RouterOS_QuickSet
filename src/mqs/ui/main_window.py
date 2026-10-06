"""Main window: navigation on the left, pages on the right, dialect header and status bar."""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mqs.apply.session import DeviceSession, end_session
from mqs.logging_setup import SecretRegistry
from mqs.messages_cs import format_message
from mqs.model.presets import apply_preset
from mqs.model.profile import DeviceProfile, Mode
from mqs.paths import profiles_dir
from mqs.profiles.store import ProfileError, ProfileStore, export_profile, import_profile
from mqs.ui import strings_cs as S
from mqs.ui.controller import Analysis, ProfileController
from mqs.ui.dialogs import Dialogs
from mqs.ui.pages.apply import ApplyPage
from mqs.ui.pages.basic import BasicPage
from mqs.ui.pages.device import DevicePage, connection_summary
from mqs.ui.pages.firewall import FirewallPage
from mqs.ui.pages.lan_bridge import LanBridgePage
from mqs.ui.pages.network import NetworkPage
from mqs.ui.pages.preview import PreviewPage
from mqs.ui.pages.services import ServicesPage
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
        device_kwargs: dict[str, Any] | None = None,
        apply_kwargs: dict[str, Any] | None = None,
        network_kwargs: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(S.APP_TITLE)
        self.resize(1280, 860)
        self.dialogs = dialogs or Dialogs()
        self.store = store or ProfileStore(profiles_dir())
        self.registry = registry or SecretRegistry()
        self.runner = TaskRunner(self)
        self.controller = ProfileController(self)

        self.device_page = DevicePage(self.controller, self.runner, self.dialogs, self.registry,
                                      **(device_kwargs or {}))
        self.network_page = NetworkPage(self.controller, self.runner, self.dialogs,
                                        **(network_kwargs or {}))
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
