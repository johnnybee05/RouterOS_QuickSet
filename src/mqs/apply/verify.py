"""Post-apply verification: checklist OK/CHYBA and HW offload requested vs. actual."""

from __future__ import annotations

import time
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from ipaddress import IPv4Interface
from typing import Any

from mqs.capabilities.offload import OffloadPlan
from mqs.generator.base import GenerationResult
from mqs.generator.blocks.common import bridge_enabled, wan_link, wan_port, wan_vlan_interface
from mqs.issues import Issue, Severity
from mqs.messages_cs import format_message
from mqs.model.bridge import LanAddressing
from mqs.model.inventory import BridgePortState, WirelessSnapshot
from mqs.model.profile import DeviceProfile
from mqs.model.version import RosVersion
from mqs.model.wan import DhcpWan, StaticWan
from mqs.transport.base import CommandError, Transport
from mqs.transport.discovery import (
    collect_radios,
    configured_services,
    to_bool,
    to_optional_bool,
)

RESET_SCRIPT_ERROR = "error while running run-after-reset script"
# How RouterOS logs the line that stopped an /import: 6.49.10 "script error: failure: mstp
# requires vlan-filtering" (script,error, 2026-09-25); 7.23.7 "(ssh-cmd:bee+cet@...) Script
# Error: failure: mstp requires vlan-filtering (/interface/bridge/set ...) (...; line 18)"
# (script,error,debug, 2026-09-26). Matched without regard to case.
IMPORT_SCRIPT_ERROR = "script error"
# How often to look at the registration table while waiting for the stations to come back.
# They re-associate on their own schedule, well after the device is up again.
REGISTRATION_POLL_S = 15.0


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

    checks.extend(_check_wan_vlan(transport, profile))
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


def check_reset_script(transport: Transport, script_file: str) -> CheckResult:
    """The run-after-reset script ran to its last line, where it removes itself.

    A line that fails stops the script there and the file stays. On 7.23.7 the log then
    says "error while running run-after-reset script: bad parameter hw-offload (line 60
    column 124)" (2026-09-24), which is what the check reports.
    """
    return _check_script_ran(transport, script_file, "verify.reset_script",
                             RESET_SCRIPT_ERROR)


def check_import_script(transport: Transport, script_file: str) -> CheckResult:
    """The imported script ran to its last line, where it removes itself.

    Over the API a failing /import is a trap, but over SSH RouterOS 6.49.10 answers it
    with nothing at all (2026-09-25): an import that stopped at its bridge line was
    reported as done. The file left behind and the log say what happened.
    """
    return _check_script_ran(transport, script_file, "verify.import_script",
                             IMPORT_SCRIPT_ERROR)


def _check_script_ran(transport: Transport, script_file: str, code: str,
                      marker: str) -> CheckResult:
    names = {f.get("name", "") for f in transport.execute("/file/print")}
    if script_file not in names:
        return CheckResult(code, True, {"error": "-"})
    try:
        log = transport.execute("/log/print")
    except CommandError:
        log = []
    error = next((_from_marker(row["message"], marker) for row in reversed(log)
                  if marker in row.get("message", "").lower()), "")
    return CheckResult(code, False, {"error": error or "?"})


def _from_marker(message: str, marker: str) -> str:
    """The message from the marker on, without 7.23.7's "(ssh-cmd:user@host)" before it."""
    return message[message.lower().index(marker):]


def check_routeros_version(transport: Transport, expected: RosVersion) -> CheckResult:
    """The device runs ``expected``: the update from the Update directory took."""
    text = transport.execute("/system/resource/print")[0].get("version", "")
    try:
        ok = RosVersion.parse(text) == expected
    except ValueError:
        ok = False
    return CheckResult("verify.routeros_version", ok,
                       {"expected": str(expected), "actual": text or "-"})


def check_package_versions(transport: Transport, expected: RosVersion,
                           disabled_before: Collection[str] = ()) -> CheckResult:
    """Every installed package is on ``expected``, and nothing is waiting for a reboot.

    A `wireless` left on 7.15.3 next to a `routeros` on 7.16.1 is the state that costs a
    radio, and RouterOS does not complain about it — so it is read back and checked. A
    package `scheduled` for enable, disable or uninstall is the same problem one reboot
    away, and is reported with the rest.

    A disabled package counts only when the update disabled it: ``disabled_before`` names
    the ones that already were, which the update left as it found them. The v6 hEX S with
    `ipv6` switched off by its profile failed this check on every hop until it was said
    (2026-10-12).
    """
    rows = transport.execute("/system/package/print")
    wrong: list[str] = []
    waiting: list[str] = []
    for row in rows:
        name = row.get("name", "")
        version = row.get("version", "")
        available = to_bool(row, "available", "A")
        if not name or available or not version:
            continue  # offered for download, not installed: not ours to check
        if row.get("scheduled"):
            waiting.append(f"{name} ({row['scheduled']})")
        elif to_bool(row, "disabled", "X") and name not in disabled_before:
            waiting.append(f"{name} (disabled)")
        try:
            same = RosVersion.parse(version) == expected
        except ValueError:
            same = False
        if not same:
            wrong.append(f"{name} {version}")
    return CheckResult("verify.package_versions", not wrong and not waiting,
                       {"expected": str(expected),
                        "wrong": ", ".join(sorted(wrong)) or "-",
                        "waiting": ", ".join(sorted(waiting)) or "-"})


def compare_radios(before: WirelessSnapshot, after: WirelessSnapshot) -> CheckResult:
    """The radios are the ones that were there before the reboot.

    Three ways they can fail to be, in the order they matter: a **menu** that is gone means
    the driver did not come back at all; an **interface** that is gone means the driver
    loaded and the radio did not; one that is **no longer running** means both happened and
    the link is still down. The first two are what this whole feature is about, so all
    three are reported together rather than one at a time.

    Extra interfaces are not a failure — a driver that renames or splits a radio across a
    version is not a loss — but they are named, because somebody has to look at the
    configuration afterwards.
    """
    lost_menus = sorted(m.value for m in set(before.menus) - set(after.menus))
    lost = sorted(set(before.names) - set(after.names))
    appeared = sorted(set(after.names) - set(before.names))
    # Only radios that were up before: one that was down stays this flow's business
    # exactly as much as it was, which is not at all.
    stopped = sorted(set(before.running_names) & set(after.names)
                     - set(after.running_names))
    return CheckResult(
        "verify.radios",
        not lost_menus and not lost and not stopped,
        {"before": ", ".join(before.names) or "-",
         "menus_lost": ", ".join(lost_menus) or "-",
         "lost": ", ".join(lost) or "-",
         "stopped": ", ".join(stopped) or "-",
         "new": ", ".join(appeared) or "-"},
    )


def registrations_returned(before: WirelessSnapshot,
                           after: WirelessSnapshot) -> tuple[bool, list[str]]:
    """Whether every radio that had stations has them again, and which do not.

    Only radios that carried stations before are asked about: an AP with nobody on it had
    an empty table to begin with, and waiting for it to fill would wait forever.
    """
    empty = [name for name, count in sorted(before.registrations.items())
             if count and not after.registrations.get(name)]
    return not empty, empty


def wait_for_registrations(
    transport: Transport,
    before: WirelessSnapshot,
    version: RosVersion,
    *,
    sleep: Callable[[float], None],
    timeout_s: float,
    interval_s: float = REGISTRATION_POLL_S,
    now: Callable[[], float] = time.monotonic,
    on_wait: Callable[[int, int], None] = lambda returned, expected: None,
) -> CheckResult | None:
    """Give the stations time to come back, and say whether they did.

    None when there is nothing to wait for: no radio carried a station before the reboot.
    A station takes its own time to re-associate — longer than the device takes to boot —
    so this is asked repeatedly rather than once, and it returns as soon as every radio
    that had stations has them again.
    """
    expected = [name for name, count in before.registrations.items() if count]
    if not expected:
        return None
    deadline = now() + timeout_s
    returned: list[str] = []
    missing = list(expected)
    while True:
        after = collect_radios(transport, version)
        ok, missing = registrations_returned(before, after)
        returned = [n for n in expected if n not in missing]
        on_wait(len(returned), len(expected))
        if ok or now() >= deadline:
            return CheckResult("verify.registrations", ok,
                               {"radios": ", ".join(sorted(expected)),
                                "empty": ", ".join(sorted(missing)) or "-",
                                "waited": f"{timeout_s:.0f}"})
        sleep(interval_s)


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
    link = wan_link(profile)  # the VLAN interface when the WAN is tagged
    if link is not None and isinstance(profile.wan.settings, DhcpWan):
        interfaces.append(link)
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


def _check_wan_vlan(transport: Transport, profile: DeviceProfile) -> list[CheckResult]:
    """The VLAN interface of a tagged WAN is on the WAN port with the right id, enabled."""
    name = wan_vlan_interface(profile)
    if name is None:
        return []
    port = wan_port(profile)
    vlan_id = str(profile.wan.vlan_id)
    try:
        rows = transport.execute("/interface/vlan/print")
    except CommandError:
        rows = []
    row = next((r for r in rows if r.get("name") == name), None)
    ok = (row is not None and row.get("vlan-id") == vlan_id and row.get("interface") == port
          and not to_bool(row, "disabled", "X"))
    return [CheckResult("verify.wan_vlan", ok,
                        {"name": name, "vlan": vlan_id, "port": port or "-"})]


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
