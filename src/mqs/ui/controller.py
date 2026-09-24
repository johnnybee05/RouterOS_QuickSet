"""Holds the edited profile and device state; recomputes validation and previews."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QTimer, Signal

from mqs.generator.base import GenerationResult, generate
from mqs.generator.context import TargetContext
from mqs.issues import Issue, has_errors
from mqs.model.inventory import DeviceInventory, Ipv6PackageState
from mqs.model.ports import RENAMEABLE_TYPES, adopt_device_names, final_port_names
from mqs.model.profile import DeviceProfile
from mqs.model.validation import validate_profile
from mqs.model.version import RosVersion

DEFAULT_OFFLINE = {6: "6.49.10", 7: "7.15.3"}
DEFAULT_PORTS = [f"ether{i}" for i in range(1, 6)]


@dataclass(frozen=True)
class Analysis:
    profile: DeviceProfile
    validation: list[Issue]
    results: dict[int, GenerationResult]
    primary_major: int
    online: bool
    probes: Mapping[str, bool] = field(default_factory=dict)

    @property
    def primary(self) -> GenerationResult:
        return self.results[self.primary_major]

    @property
    def all_issues(self) -> list[Issue]:
        return [*self.validation, *self.primary.issues]

    @property
    def can_apply(self) -> bool:
        return not has_errors(self.all_issues)


class ProfileController(QObject):
    profileChanged = Signal(object, object)
    inventoryChanged = Signal(object)
    analysisReady = Signal(object)

    def __init__(self, parent: QObject | None = None, debounce_ms: int = 250) -> None:
        super().__init__(parent)
        self._profile = DeviceProfile()
        self._inventory: DeviceInventory | None = None
        self._probes: dict[str, bool] = {}
        self.offline_version = RosVersion.parse(DEFAULT_OFFLINE[7])
        self.offline_ports: list[str] = list(DEFAULT_PORTS)
        self.offline_ipv6 = Ipv6PackageState.UNKNOWN
        self.analysis: Analysis | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(debounce_ms)
        self._timer.timeout.connect(self.recompute)

    # ---------------------------------------------------------------- profile

    @property
    def profile(self) -> DeviceProfile:
        return self._profile

    def set_profile(self, profile: DeviceProfile, source: object | None = None) -> None:
        if self._inventory is not None:
            adopted = adopt_device_names(profile, self._inventory)
            if adopted is not profile:
                profile, source = adopted, None  # changed beyond the edit: reload everywhere
        self._profile = profile
        self.profileChanged.emit(profile, source)
        self._timer.start()

    def edit(self, change: Callable[[DeviceProfile], DeviceProfile],
             source: object | None = None) -> None:
        self.set_profile(change(self._profile), source)

    # ---------------------------------------------------------------- device state

    @property
    def inventory(self) -> DeviceInventory | None:
        return self._inventory

    @property
    def probes(self) -> dict[str, bool]:
        return dict(self._probes)

    def set_inventory(self, inventory: DeviceInventory | None,
                      probes: Mapping[str, bool] | None = None) -> None:
        self._inventory = inventory
        self._probes = dict(probes or {})
        profile = self._profile
        if inventory is not None:
            self._profile = adopt_device_names(profile, inventory)
        self.inventoryChanged.emit(inventory)
        if self._profile is not profile:  # the device's port names taken over
            self.profileChanged.emit(self._profile, None)
        self._timer.start()

    def set_offline_target(self, version: str | None = None, ports: list[str] | None = None,
                           ipv6: Ipv6PackageState | None = None) -> None:
        if version is not None:
            self.offline_version = RosVersion.parse(version)
        if ports is not None:
            self.offline_ports = ports
        if ipv6 is not None:
            self.offline_ipv6 = ipv6
        self.inventoryChanged.emit(self._inventory)
        self._timer.start()

    def ports(self, profile: DeviceProfile | None = None) -> list[str]:
        """Ports selectable as WAN or bridge member, by their names in ``profile``."""
        if profile is None:
            profile = self._profile
        if self._inventory is not None:
            return final_port_names(profile, self._inventory)
        return [profile.interface_names.get(p, p) for p in self.offline_ports]

    def port_rows(self) -> list[tuple[str, str, str | None]]:
        """(default-name, name in the profile, name on the device) of the ports that can
        be renamed, then those the profile names but the target does not have."""
        names = self._profile.interface_names
        if self._inventory is not None:
            rows = [(i.default_name, names.get(i.default_name, i.name), i.name)
                    for i in self._inventory.interfaces
                    if i.type in RENAMEABLE_TYPES and i.default_name]
        else:
            rows = [(p, names.get(p, p), None) for p in self.offline_ports]
        known = {default for default, _, _ in rows}
        return rows + [(d, n, None) for d, n in names.items() if d not in known]

    def default_names(self) -> dict[str, str]:
        """Port name in the profile -> its default-name, where the two differ."""
        return {name: default for default, name, _ in self.port_rows() if name != default}

    @property
    def primary_major(self) -> int:
        version = self._inventory.version if self._inventory else self.offline_version
        return 6 if version.major == 6 else 7

    def context(self, major: int) -> TargetContext:
        if self._inventory is not None and self._inventory.version.major == major:
            return TargetContext.from_inventory(self._inventory, self._probes)
        if self._inventory is None and self.offline_version.major == major:
            return TargetContext.offline(self.offline_version, ipv6_package=self.offline_ipv6)
        return TargetContext.offline(DEFAULT_OFFLINE[major], ipv6_package=self.offline_ipv6)

    # ---------------------------------------------------------------- analysis

    def recompute(self) -> Analysis:
        self._timer.stop()
        profile = self._profile
        results = {major: generate(profile, self.context(major), mask_secrets=True)
                   for major in (6, 7)}
        self.analysis = Analysis(
            profile=profile,
            validation=validate_profile(profile, self._inventory),
            results=results,
            primary_major=self.primary_major,
            online=self._inventory is not None,
            probes=dict(self._probes),
        )
        self.analysisReady.emit(self.analysis)
        return self.analysis

    def generate_for_apply(self, update: RosVersion | None = None) -> GenerationResult:
        """Unmasked script for the device (passwords included); for the version an
        update installs first, when there is one."""
        context = self.context(self.primary_major)
        return generate(self._profile, context if update is None
                        else context.after_update(update))
