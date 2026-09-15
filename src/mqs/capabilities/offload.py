"""Per-port bridge HW offload decisions (spec 4.8).

FastTrack is a separate, unrelated feature and is handled by the firewall block.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from mqs.capabilities.chips import ChipDatabase, Tri, default_chip_database
from mqs.capabilities.matrix import Capabilities, PortKind
from mqs.generator.features import version_supports
from mqs.issues import Issue, Severity
from mqs.model.bridge import BridgeConfig, HwOffloadMode, ProtocolMode
from mqs.model.version import RosVersion

_FIELD = "bridge.hw_offload"


class OffloadStatus(StrEnum):
    YES = "yes"
    NO = "no"
    UNCERTAIN = "uncertain"  # hw=yes is requested, result must be verified after apply
    NOT_OFFERED = "not-offered"  # version without hw= (< 6.41): emit nothing


@dataclass(frozen=True)
class PortOffload:
    port: str
    status: OffloadStatus
    reason: str  # message code
    switch: str | None = None
    chip_type: str | None = None
    params: dict[str, Any] = field(default_factory=dict, hash=False)

    @property
    def hw(self) -> bool | None:
        """Value for `hw=`; None means the parameter must not be emitted."""
        if self.status == OffloadStatus.NOT_OFFERED:
            return None
        return self.status in (OffloadStatus.YES, OffloadStatus.UNCERTAIN)


@dataclass(frozen=True)
class OffloadGroup:
    switch: str
    chip_type: str
    ports: tuple[str, ...]


@dataclass(frozen=True)
class OffloadPlan:
    offered: bool
    ports: tuple[PortOffload, ...]
    groups: tuple[OffloadGroup, ...]
    software_ports: tuple[str, ...]
    software_bridge: bool
    issues: tuple[Issue, ...]

    def for_port(self, port: str) -> PortOffload | None:
        return next((p for p in self.ports if p.port == port), None)

    @property
    def has_conflict(self) -> bool:
        return any(i.code == "offload.mstp_conflict" for i in self.issues)


_KIND_REASONS = {
    PortKind.SFP: "offload.port_sfp",
    PortKind.WIRELESS: "offload.port_wireless",
    PortKind.BONDING: "offload.port_bonding",
    PortKind.VLAN: "offload.port_other",
    PortKind.OTHER: "offload.port_other",
}
_FORCEABLE_KINDS = {PortKind.SFP, PortKind.BONDING}


def plan_offload_without_hardware(bridge: BridgeConfig, version: RosVersion) -> OffloadPlan:
    """Offline generation with no switch-chip data.

    AUTO emits no `hw=` at all (RouterOS keeps its own default), OFF emits `hw=no`,
    FORCE_ON emits `hw=yes`; the real state is verified after apply.
    """
    empty = Capabilities(version=version)
    if not version_supports("bridge_port_hw", version) or bridge.hw_offload == HwOffloadMode.OFF:
        return plan_offload(bridge, empty)
    if bridge.hw_offload == HwOffloadMode.AUTO:
        ports = tuple(
            PortOffload(p, OffloadStatus.NOT_OFFERED, "offload.no_device_data")
            for p in bridge.ports
        )
        issues = (Issue("offload.no_device_data", Severity.INFO, _FIELD),) if ports else ()
        return OffloadPlan(False, ports, (), (), False, issues)
    ports = tuple(
        PortOffload(p, OffloadStatus.YES, "offload.forced",
                    params={"port": p, "original": "offload.no_device_data"})
        for p in bridge.ports
    )
    issues = tuple(
        Issue("offload.forced", Severity.WARNING, _FIELD, dict(p.params)) for p in ports
    )
    return OffloadPlan(True, ports, (), tuple(bridge.ports), False, issues)


def plan_offload(
    bridge: BridgeConfig,
    capabilities: Capabilities,
    chip_db: ChipDatabase | None = None,
) -> OffloadPlan:
    chip_db = chip_db or default_chip_database()
    version = capabilities.version
    issues: list[Issue] = []

    if not version_supports("bridge_port_hw", version):
        ports = tuple(
            PortOffload(p, OffloadStatus.NOT_OFFERED, "offload.version_too_old",
                        params={"version": str(version)})
            for p in bridge.ports
        )
        if bridge.hw_offload != HwOffloadMode.OFF and bridge.ports:
            issues.append(
                Issue("offload.version_too_old", Severity.INFO, _FIELD, {"version": str(version)})
            )
        return OffloadPlan(False, ports, (), (), False, tuple(issues))

    decisions: list[PortOffload] = []
    conflict_chips: dict[str, str] = {}
    forced = bridge.hw_offload == HwOffloadMode.FORCE_ON

    for name in bridge.ports:
        cap = capabilities.port(name)
        base = {"switch": cap.switch, "chip_type": cap.chip_type}
        params: dict[str, Any] = {"chip": cap.switch or "", "chip_type": cap.chip_type or "",
                                  "type": cap.type}

        def decide(status: OffloadStatus, reason: str, forceable: bool = True,
                   _base=base, _params=params, _name=name) -> PortOffload:
            if forced and forceable and status != OffloadStatus.YES:
                return PortOffload(_name, OffloadStatus.YES, "offload.forced",
                                   params={**_params, "port": _name, "original": reason}, **_base)
            return PortOffload(_name, status, reason, params=_params, **_base)

        if bridge.hw_offload == HwOffloadMode.OFF:
            decisions.append(decide(OffloadStatus.NO, "offload.disabled_by_user", False))
            continue
        if cap.kind != PortKind.ETHERNET:
            decisions.append(
                decide(OffloadStatus.NO, _KIND_REASONS[cap.kind], cap.kind in _FORCEABLE_KINDS)
            )
            continue
        if cap.switch is None or cap.chip_type is None:
            decisions.append(decide(OffloadStatus.NO, "offload.port_no_switch", False))
            continue
        if bridge.use_ip_firewall:
            decisions.append(decide(OffloadStatus.NO, "offload.use_ip_firewall", False))
            continue

        chip = chip_db.lookup(cap.chip_type)
        if chip.hw_offload == Tri.NO:
            decisions.append(decide(OffloadStatus.NO, "offload.chip_unsupported"))
            continue
        if bridge.protocol_mode == ProtocolMode.MSTP and chip.mstp != Tri.YES:
            conflict_chips[cap.switch] = cap.chip_type
            decisions.append(decide(OffloadStatus.NO, "offload.mstp_unsupported", False))
            continue
        if bridge.vlan_filtering:
            support = chip.vlan_filtering(version.major)
            if support == Tri.NO:
                decisions.append(decide(OffloadStatus.NO, "offload.vlan_filtering_unsupported"))
                continue
            if support == Tri.UNKNOWN:
                decisions.append(
                    decide(OffloadStatus.UNCERTAIN, "offload.vlan_filtering_uncertain")
                )
                continue
        reason = "offload.ok" if chip.known else "offload.ok_unknown_chip"
        decisions.append(decide(OffloadStatus.YES, reason))

    for switch, chip_type in sorted(conflict_chips.items()):
        issues.append(
            Issue("offload.mstp_conflict", Severity.ERROR, "bridge.protocol_mode",
                  {"chip": switch, "chip_type": chip_type})
        )
    if bridge.use_ip_firewall and bridge.hw_offload != HwOffloadMode.OFF:
        issues.append(Issue("offload.use_ip_firewall", Severity.WARNING, "bridge.use_ip_firewall"))
    for d in decisions:
        if d.reason == "offload.forced":
            issues.append(Issue("offload.forced", Severity.WARNING, _FIELD,
                                {"port": d.port, "original": d.params["original"]}))
        elif d.status == OffloadStatus.UNCERTAIN:
            issues.append(Issue("offload.vlan_filtering_uncertain", Severity.WARNING, _FIELD,
                                d.params))

    grouped: dict[str, list[PortOffload]] = {}
    for d in decisions:
        if d.hw and d.switch is not None:
            grouped.setdefault(d.switch, []).append(d)
    groups = tuple(
        OffloadGroup(switch, members[0].chip_type or "", tuple(m.port for m in members))
        for switch, members in sorted(grouped.items())
    )
    software_ports = tuple(d.port for d in decisions if not (d.hw and d.switch is not None))
    software_bridge = len(groups) > 1 or (bool(groups) and bool(software_ports))

    if len(groups) > 1:
        chips = ", ".join(f"{g.switch} ({g.chip_type}: {', '.join(g.ports)})" for g in groups)
        issues.append(Issue("offload.software_bridge", Severity.INFO, _FIELD, {"chips": chips}))
    if groups and software_ports:
        issues.append(Issue("offload.mixed_software", Severity.INFO, _FIELD,
                            {"ports": ", ".join(software_ports)}))

    return OffloadPlan(
        offered=True,
        ports=tuple(decisions),
        groups=groups,
        software_ports=software_ports,
        software_bridge=software_bridge,
        issues=tuple(issues),
    )
