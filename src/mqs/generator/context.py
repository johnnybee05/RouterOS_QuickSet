"""Generation target: RouterOS version plus whatever is known about the device."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from mqs.generator.features import FEATURES, Feature, version_supports
from mqs.model.inventory import DeviceInventory, Ipv6PackageState
from mqs.model.version import RosVersion


@dataclass(frozen=True)
class TargetContext:
    version: RosVersion
    inventory: DeviceInventory | None = None
    # Results of runtime probes, e.g. {"fasttrack_hw_offload": True}. Missing = not confirmed.
    probes: Mapping[str, bool] = field(default_factory=dict)
    # Used when offline (no inventory).
    assumed_ipv6_package: Ipv6PackageState = Ipv6PackageState.UNKNOWN
    # Features consulted during generation (for "NUTNO OVĚŘIT" reporting).
    consulted: set[str] = field(default_factory=set, compare=False, repr=False)

    @classmethod
    def offline(
        cls,
        version: str | RosVersion,
        *,
        ipv6_package: Ipv6PackageState = Ipv6PackageState.UNKNOWN,
        probes: Mapping[str, bool] | None = None,
    ) -> TargetContext:
        if isinstance(version, str):
            version = RosVersion.parse(version)
        if version.major >= 7:
            ipv6_package = Ipv6PackageState.BUILTIN
        return cls(version=version, assumed_ipv6_package=ipv6_package, probes=probes or {})

    @classmethod
    def from_inventory(
        cls, inventory: DeviceInventory, probes: Mapping[str, bool] | None = None
    ) -> TargetContext:
        return cls(version=inventory.version, inventory=inventory, probes=probes or {})

    def after_update(self, version: RosVersion) -> TargetContext:
        """The device as its script will find it once an update has installed ``version``.

        No runtime probe carries over: it measured the version that is going away. The
        hEX S had `hw-offload` on a filter rule in 7.20.7, and 7.23.7 refused the same
        line with "bad parameter hw-offload" (2026-09-24). Unconfirmed means left out.
        """
        return replace(self, version=version, probes={}, consulted=set())

    @property
    def major(self) -> int:
        return self.version.major

    @property
    def online(self) -> bool:
        return self.inventory is not None

    @property
    def ipv6_package(self) -> Ipv6PackageState:
        if self.inventory is not None:
            return self.inventory.ipv6_package_state
        return self.assumed_ipv6_package

    def has(self, key: str) -> bool:
        """Feature available on the target version (and confirmed by probe when required)."""
        self.consulted.add(key)
        if not version_supports(key, self.version):
            return False
        if FEATURES[key].runtime_probe:
            return bool(self.probes.get(key, False))
        return True

    def unverified_consulted(self) -> list[Feature]:
        return [FEATURES[k] for k in sorted(self.consulted) if not FEATURES[k].verified]
