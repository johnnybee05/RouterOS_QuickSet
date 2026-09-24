"""Post-apply verification: checklist OK/CHYBA and HW offload requested vs. actual."""

from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import IPv4Interface
from typing import Any

from mqs.capabilities.offload import OffloadPlan
from mqs.generator.base import GenerationResult
from mqs.generator.blocks.common import bridge_enabled, wan_port
from mqs.issues import Issue, Severity
from mqs.messages_cs import format_message
from mqs.model.bridge import LanAddressing
from mqs.model.inventory import BridgePortState
from mqs.model.profile import DeviceProfile
from mqs.model.version import RosVersion
from mqs.model.wan import DhcpWan, StaticWan
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

    checks.extend(_check_default_route(transport, profile))
    checks.extend(_check_dhcp_clients(transport, profile))

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


def check_routeros_version(transport: Transport, expected: RosVersion) -> CheckResult:
    """The device runs ``expected``: the update from the Update directory took."""
    text = transport.execute("/system/resource/print")[0].get("version", "")
    try:
        ok = RosVersion.parse(text) == expected
    except ValueError:
        ok = False
    return CheckResult("verify.routeros_version", ok,
                       {"expected": str(expected), "actual": text or "-"})


def expected_gateways(profile: DeviceProfile) -> list[str]:
    """Gateways of the static default routes the profile adds (static WAN, LAN gateway)."""
    gateways = []
    settings = profile.wan.settings
    if wan_port(profile) is not None and isinstance(settings, StaticWan):
        gateways.append(str(settings.gateway))
    lan = profile.lan
    if (bridge_enabled(profile) and lan.addressing == LanAddressing.STATIC and lan.address
            and lan.gateway is not None):
        gateways.append(str(lan.gateway))
    return gateways


def expected_dhcp_clients(profile: DeviceProfile) -> list[str]:
    """Interfaces the profile runs a DHCP client on (DHCP WAN, LAN address by DHCP)."""
    interfaces = []
    port = wan_port(profile)
    if port is not None and isinstance(profile.wan.settings, DhcpWan):
        interfaces.append(port)
    if bridge_enabled(profile) and profile.lan.addressing == LanAddressing.DHCP_CLIENT:
        interfaces.append(profile.bridge.name)
    return interfaces


def _check_default_route(transport: Transport, profile: DeviceProfile) -> list[CheckResult]:
    """The static default routes on the device are exactly the profile's.

    An import used to add a second default route next to the old one; both were active
    and the checklist, which did not look at routes, said OK (hEX S, 2026-09-23).
    Dynamic routes (DHCP, PPPoE) come and go with their lease and are not judged.
    """
    expected = expected_gateways(profile)
    if not expected:
        return []
    try:
        rows = transport.execute("/ip/route/print")
    except CommandError:
        rows = None
    actual = sorted(
        r.get("gateway", "") for r in rows or []
        if r.get("dst-address") == "0.0.0.0/0" and r.get("routing-table", "main") == "main"
        and not to_bool(r, "dynamic", "D") and not to_bool(r, "disabled", "X"))
    return [CheckResult("verify.default_route", rows is not None and actual == sorted(expected),
                        {"expected": ", ".join(expected), "actual": ", ".join(actual) or "-"})]


def _check_dhcp_clients(transport: Transport, profile: DeviceProfile) -> list[CheckResult]:
    """A DHCP client is there and enabled; its status only says how far the lease got."""
    interfaces = expected_dhcp_clients(profile)
    if not interfaces:
        return []
    try:
        rows = transport.execute("/ip/dhcp-client/print")
    except CommandError:
        rows = []
    checks = []
    for interface in interfaces:
        client = next((r for r in rows if r.get("interface") == interface), None)
        enabled = client is not None and not to_bool(client, "disabled", "X")
        status = client.get("status", "") if client is not None else ""
        checks.append(CheckResult("verify.dhcp_client", enabled,
                                  {"interface": interface, "status": status or "-"}))
    return checks


def _same_address(value: str, expected: IPv4Interface) -> bool:
    try:
        return IPv4Interface(value) == expected
    except ValueError:
        return False
