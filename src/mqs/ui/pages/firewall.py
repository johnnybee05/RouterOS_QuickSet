"""Firewall: basic filter, IPv6 and FastTrack (HW offload of the bridge is on the LAN page)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QCheckBox, QGroupBox, QLabel, QMessageBox, QVBoxLayout, QWidget

from mqs.model.profile import DeviceProfile
from mqs.ui import strings as S
from mqs.ui.controller import ProfileController
from mqs.ui.pages.base import Page, update_in


def ask_uses_shaping(parent: QWidget) -> bool:
    answer = QMessageBox.question(parent, S.FASTTRACK_SHAPING_TITLE, S.FASTTRACK_SHAPING_QUESTION)
    return answer == QMessageBox.StandardButton.Yes


class FirewallPage(Page):
    def __init__(self, controller: ProfileController, parent: QWidget | None = None,
                 ask_shaping: Callable[[QWidget], bool] = ask_uses_shaping) -> None:
        super().__init__(controller, parent)
        self.ask_shaping = ask_shaping
        layout = QVBoxLayout(self)

        self.basic_filter = QCheckBox(S.FIREWALL_BASIC)
        self.basic_filter.toggled.connect(
            lambda on: self.push(lambda profile: profile.with_basic_filter(on)))
        layout.addWidget(self.basic_filter)
        basic_help = QLabel(S.FIREWALL_BASIC_HELP)
        basic_help.setWordWrap(True)
        layout.addWidget(basic_help)
        self.wan_management = QCheckBox(S.FIREWALL_WAN_MANAGEMENT)
        self.wan_management.toggled.connect(
            lambda on: self.push_value("firewall.wan_management", on))
        layout.addWidget(self.wan_management)
        wan_help = QLabel(S.FIREWALL_WAN_MANAGEMENT_HELP)
        wan_help.setWordWrap(True)
        layout.addWidget(wan_help)
        self.ipv6 = QCheckBox(S.FIREWALL_IPV6)
        self.ipv6.toggled.connect(lambda on: self.push_value("firewall.ipv6", on))
        layout.addWidget(self.ipv6)
        ipv6_help = QLabel(S.FIREWALL_IPV6_HELP)
        ipv6_help.setWordWrap(True)
        layout.addWidget(ipv6_help)

        box = QGroupBox(S.FASTTRACK_GROUP)
        inner = QVBoxLayout(box)
        self.fasttrack = QCheckBox(S.FASTTRACK_ENABLED)
        self.fasttrack.toggled.connect(self._fasttrack_toggled)
        inner.addWidget(self.fasttrack)
        self.fasttrack_hw = QCheckBox(S.FASTTRACK_HW)
        self.fasttrack_hw.toggled.connect(
            lambda on: self.push_value("firewall.fasttrack.hw_offload", on))
        inner.addWidget(self.fasttrack_hw)
        help_label = QLabel(S.FASTTRACK_HELP)
        help_label.setWordWrap(True)
        inner.addWidget(help_label)
        layout.addWidget(box)
        layout.addStretch(1)

        self.fields = {"firewall.fasttrack.enabled": self.fasttrack,
                       "firewall.basic_filter": self.basic_filter,
                       "firewall.wan_management": self.wan_management,
                       "firewall.ipv6": self.ipv6}
        self.reload()

    def _fasttrack_toggled(self, on: bool) -> None:
        if self._loading:
            return
        if not on:
            self.push_value("firewall.fasttrack.enabled", False)
            return
        uses_shaping = self.ask_shaping(self)
        if uses_shaping:
            # FastTrack would bypass the user's queues: keep it off.
            self._loading = True
            self.fasttrack.setChecked(False)
            self._loading = False
            return

        def change(profile: DeviceProfile) -> DeviceProfile:
            profile = update_in(profile, "firewall.fasttrack.enabled", True)
            return update_in(profile, "firewall.fasttrack.no_shaping_confirmed", True)

        self.push(change)

    def load(self, profile: DeviceProfile) -> None:
        self.basic_filter.setChecked(profile.firewall.basic_filter)
        self.wan_management.setChecked(profile.firewall.wan_management)
        self.ipv6.setChecked(profile.firewall.ipv6)
        self.fasttrack.setChecked(profile.firewall.fasttrack.enabled)
        self.fasttrack_hw.setChecked(profile.firewall.fasttrack.hw_offload)

    def refresh_state(self, profile: DeviceProfile) -> None:
        self.fasttrack_hw.setEnabled(profile.firewall.fasttrack.enabled)
        filtered = profile.firewall.basic_filter or profile.firewall.ipv6
        self.wan_management.setEnabled(filtered and profile.wan.enabled)
