"""Post-apply verification: checklist OK/CHYBA and HW offload requested vs. actual."""

from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import IPv4Interface
from typing import Any

from mqs.capabilities.offload import OffloadPlan
from mqs.generator.base import GenerationResult
from mqs.generator.blocks.common import bridge_enabled
from mqs.issues import Issue, Severity
from mqs.messages_cs import format_message
from mqs.model.bridge import LanAddressing
from mqs.model.inventory import BridgePortState
from mqs.model.profile import DeviceProfile
from mqs.transport.base import CommandError, Transport
from mqs.transport.discovery import configured_services, to_bool, to_optional_bool


@dataclass(frozen=True)
class CheckResult:
    code: str
    ok: bool
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> str:
        return format_message(self.code, self.params)


def read_bridge_ports(transport: Transport) -> list[BridgePortState]:
    return [
        BridgePortState(
            interface=p.get("interface", ""),
            bridge=p.get("bridge", ""),
            hw=to_optional_bool(p, "hw"),
            hw_offload=to_optional_bool(p, "hw-offload", "H"),
            disabled=to_bool(p, "disabled", "X"),
        )
        for p in transport.execute("/interface/bridge/port/print", detail=True)
    ]


def compare_offload(plan: OffloadPlan, actual: list[BridgePortState]) -> list[Issue]:
    """RouterOS silently accepts hw=yes where offload does not run; report every difference."""
    issues: list[Issue] = []
    by_port = {p.interface: p for p in actual}
    for decision in plan.ports:
        if decision.hw is None:
            continue
        state = by_port.get(decision.port)
        reason = format_message(decision.reason, decision.params)
        if state is None:
            issues.append(Issue("verify.offload_port_missing", Severity.WARNING, "",
                                {"port": decision.port}))
        elif decision.hw and state.hw_offload is not True:
            issues.append(Issue("verify.offload_not_active", Severity.WARNING, "",
                                {"port": decision.port, "reason": reason}))
        elif not decision.hw and state.hw_offload is True:
            issues.append(Issue("verify.offload_unexpected", Severity.INFO, "",
                                {"port": decision.port}))
    return issues


def verify_configuration(
    transport: Transport, profile: DeviceProfile, result: GenerationResult
) -> tuple[list[CheckResult], list[Issue]]:
    checks: list[CheckResult] = []
    issues: list[Issue] = []

    identity = transport.execute("/system/identity/print")[0].get("name", "")
    checks.append(CheckResult("verify.identity", identity == profile.identity,
                              {"expected": profile.identity, "actual": identity}))

    rows = configured_services(transport.execute("/ip/service/print"))
    device_services = {s["name"]: s for s in rows if "name" in s}
    wrong = []
    for name, expected in sorted(result.access.services.items()):
        actual = device_services.get(name)
        if actual is None:
            continue
        if (to_bool(actual, "disabled", "X") != expected.disabled
                or int(actual.get("port", "0") or 0) != expected.port):
            wrong.append(name)
    checks.append(CheckResult("verify.services", not wrong,
                              {"services": ", ".join(wrong) or "-"}))

    if bridge_enabled(profile):
        bridge = next((b for b in transport.execute("/interface/bridge/print")
                       if b.get("name") == profile.bridge.name), None)
        mode = bridge.get("protocol-mode", "") if bridge else ""
        checks.append(CheckResult(
            "verify.bridge",
            bridge is not None and mode == profile.bridge.protocol_mode.value,
            {"name": profile.bridge.name, "expected": profile.bridge.protocol_mode.value,
             "actual": mode or "-"},
        ))

    lan = profile.lan
    if bridge_enabled(profile) and lan.addressing == LanAddressing.STATIC and lan.address:
        present = any(
            _same_address(a.get("address", ""), lan.address) and a.get("interface")
            == profile.bridge.name
            for a in transport.execute("/ip/address/print")
        )
        checks.append(CheckResult("verify.lan_address", present,
                                  {"address": str(lan.address), "bridge": profile.bridge.name}))

    if result.offload.offered and any(p.hw is not None for p in result.offload.ports):
        try:
            offload_issues = compare_offload(result.offload, read_bridge_ports(transport))
        except CommandError as exc:
            offload_issues = [Issue("verify.offload_unreadable", Severity.WARNING, "",
                                    {"error": exc.message})]
        issues.extend(offload_issues)
        checks.append(CheckResult("verify.hw_offload", not any(
            i.severity == Severity.WARNING for i in offload_issues),
            {"count": len(offload_issues)}))
    return checks, issues


def _same_address(value: str, expected: IPv4Interface) -> bool:
    try:
        return IPv4Interface(value) == expected
    except ValueError:
        return False
