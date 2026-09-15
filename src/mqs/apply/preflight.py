"""Checks before anything is changed on the device."""

from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import IPv4Interface

from mqs.apply.plan import MIN_ROLLBACK_MINUTES, ApplyMode, ApplyOptions
from mqs.generator.base import GenerationResult
from mqs.generator.blocks.common import wan_port
from mqs.generator.ir import Script
from mqs.issues import Issue, Severity, has_errors
from mqs.model.inventory import DeviceInventory
from mqs.model.profile import DeviceProfile
from mqs.transport.base import CommandError, Transport

E, W, INFO = Severity.ERROR, Severity.WARNING, Severity.INFO
_MANAGEMENT = ("api-ssl", "ssh", "winbox")


@dataclass(frozen=True)
class ExistingObjects:
    bridges: frozenset[str] = frozenset()
    interface_lists: frozenset[str] = frozenset()
    pools: frozenset[str] = frozenset()
    dhcp_servers: frozenset[str] = frozenset()
    pppoe_clients: frozenset[str] = frozenset()
    addresses: frozenset[str] = frozenset()  # normalised "a.b.c.d/nn"
    dhcp_clients: frozenset[str] = frozenset()  # interfaces
    bridge_ports: dict[str, str] = field(default_factory=dict)  # interface -> bridge


def _names(transport: Transport, path: str, key: str = "name") -> frozenset[str]:
    try:
        return frozenset(i[key] for i in transport.execute(path) if key in i)
    except CommandError:
        return frozenset()


def collect_existing(transport: Transport) -> ExistingObjects:
    try:
        ports = {p["interface"]: p.get("bridge", "")
                 for p in transport.execute("/interface/bridge/port/print") if "interface" in p}
    except CommandError:
        ports = {}
    return ExistingObjects(
        bridges=_names(transport, "/interface/bridge/print"),
        interface_lists=_names(transport, "/interface/list/print"),
        pools=_names(transport, "/ip/pool/print"),
        dhcp_servers=_names(transport, "/ip/dhcp-server/print"),
        pppoe_clients=_names(transport, "/interface/pppoe-client/print"),
        addresses=frozenset(_normalise_address(a) for a in
                            _names(transport, "/ip/address/print", "address")),
        dhcp_clients=_names(transport, "/ip/dhcp-client/print", "interface"),
        bridge_ports=ports,
    )


def _normalise_address(value: str) -> str:
    try:
        return str(IPv4Interface(value))
    except ValueError:
        return value


_NAMED_ADDS = {
    "/interface bridge": ("bridges", "bridge"),
    "/interface list": ("interface_lists", "interface-list"),
    "/ip pool": ("pools", "pool"),
    "/ip dhcp-server": ("dhcp_servers", "dhcp-server"),
    "/interface pppoe-client": ("pppoe_clients", "pppoe-client"),
}


def find_collisions(script: Script, existing: ExistingObjects) -> list[Issue]:
    """Objects the script adds that already exist; `/import` would stop at the first one."""
    issues: list[Issue] = []

    def collision(kind: str, name: str, other: str = "") -> None:
        issues.append(Issue("apply.collision", W, "", {"kind": kind, "name": name,
                                                        "other": other}))

    for command in script.commands():
        if command.verb != "add":
            continue
        if command.path in _NAMED_ADDS:
            attr, kind = _NAMED_ADDS[command.path]
            name = command.arg("name")
            if name and name in getattr(existing, attr):
                collision(kind, name)
        elif command.path == "/ip address":
            address = _normalise_address(command.arg("address") or "")
            if address in existing.addresses:
                collision("ip-address", address)
        elif command.path == "/interface bridge port":
            interface = command.arg("interface") or ""
            if interface in existing.bridge_ports:
                other_bridge = existing.bridge_ports[interface]
                collision("bridge-port", interface, f" (v bridge {other_bridge})")
        elif command.path == "/ip dhcp-client":
            interface = command.arg("interface") or ""
            if interface in existing.dhcp_clients:
                collision("dhcp-client", interface)
    return issues


def preflight(
    profile: DeviceProfile,
    result: GenerationResult,
    validation: list[Issue],
    options: ApplyOptions,
    inventory: DeviceInventory | None,
    *,
    existing: ExistingObjects | None = None,
    session_port: str | None = None,
) -> list[Issue]:
    issues: list[Issue] = []
    if has_errors([*validation, *result.issues]):
        issues.append(Issue("apply.blocked_by_errors", E))
    if options.mode == ApplyMode.GENERATE_ONLY:
        return issues
    if inventory is None:
        return [*issues, Issue("apply.not_connected", E)]

    device, target = inventory.version, result.target
    if device.major != target.major:
        if options.confirm_dialect_mismatch:
            issues.append(Issue("apply.dialect_mismatch", W, "",
                                {"device": str(device), "target": str(target)}))
        else:
            issues.append(Issue("apply.dialect_mismatch_unconfirmed", E, "",
                                {"device": str(device), "target": str(target)}))
    elif device != target:
        issues.append(Issue("apply.target_version_differs", INFO, "",
                            {"device": str(device), "target": str(target)}))

    if options.mode == ApplyMode.RESET_AND_IMPORT:
        if options.confirmed_identity != inventory.identity:
            issues.append(Issue("apply.reset_identity_mismatch", E, "",
                                {"identity": inventory.identity}))
        if not options.admin_password:
            issues.append(Issue("apply.reset_password_required", E))
        if options.rollback_minutes is not None:
            issues.append(Issue("apply.rollback_ignored_reset", W))
    elif options.rollback_minutes is not None and options.rollback_minutes < MIN_ROLLBACK_MINUTES:
        issues.append(Issue("apply.rollback_too_short", E, "",
                            {"minimum": MIN_ROLLBACK_MINUTES}))

    if session_port is not None:
        if session_port == wan_port(profile):
            issues.append(Issue("apply.lockout_wan", W, "wan.interface", {"port": session_port}))
        elif (options.mode == ApplyMode.RESET_AND_IMPORT
              and session_port not in profile.bridge.ports):
            issues.append(Issue("apply.lockout_unconfigured_port", W, "bridge.ports",
                                {"port": session_port}))

    if options.mode == ApplyMode.IMPORT and existing is not None:
        issues.extend(find_collisions(result.script, existing))

    issues.extend(_notices(result))
    return issues


def _notices(result: GenerationResult) -> list[Issue]:
    """Warnings the GUI must show before applying (spec 4.5)."""
    access = result.access
    services = access.services
    notices = [Issue("apply.notice_ssh_port", INFO, "", {"port": access.ssh_port})]
    disabled = sorted(n for n, s in services.items() if s.disabled and n not in _MANAGEMENT)
    if disabled:
        notices.append(Issue("apply.notice_services_disabled", INFO, "",
                             {"services": ", ".join(disabled)}))
    if "api-ssl" in access.remaining:
        notices.append(Issue("apply.notice_api_ssl_kept", INFO, "", {"port": access.api_ssl_port}))
    if result.target.major >= 7 and services.get("www-ssl") and services["www-ssl"].disabled:
        notices.append(Issue("apply.notice_rest_api", INFO))
    paths = ", ".join(f"{n} {services[n].port}" for n in access.remaining)
    notices.append(Issue("apply.notice_remaining_paths", INFO, "",
                         {"count": len(access.remaining), "paths": paths or "-"}))
    return notices
