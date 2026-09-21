"""Remaining-access-path analysis for the final /ip service state."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from mqs.generator.hardening.parser import parse
from mqs.generator.ir import Command, Find
from mqs.issues import Issue, Severity
from mqs.model.inventory import ServiceInfo

SERVICE_MENU = "/ip service"
MANAGEMENT_SERVICES = ("api-ssl", "ssh", "winbox")
DEFAULT_SERVICE_PORTS = {
    "telnet": 23,
    "ftp": 21,
    "www": 80,
    "ssh": 22,
    "www-ssl": 443,
    "api": 8728,
    "winbox": 8291,
    "api-ssl": 8729,
}
# Services disabled on a device without configuration: the baseline offline and right
# after a reset. On 7.20.7 after `no-defaults` www-ssl was off and ssh, winbox and api-ssl
# on; the other four and v6 NUTNO OVĚŘIT (docs/DIALECT_NOTES.md).
OFFLINE_DEFAULT_DISABLED = frozenset({"www-ssl"})
_FIELD = "services.hardening_text"
NOT_DYNAMIC = ("dynamic", "no")


@dataclass(frozen=True)
class ServiceState:
    name: str
    port: int
    disabled: bool
    address: str = ""


@dataclass(frozen=True)
class AccessReport:
    services: dict[str, ServiceState]
    remaining: tuple[str, ...]
    disabled_paths: tuple[str, ...]
    uncertain: bool
    issues: tuple[Issue, ...]

    @property
    def blocked(self) -> bool:
        return not self.remaining

    @property
    def ssh_port(self) -> int:
        return self.services["ssh"].port

    @property
    def api_ssl_port(self) -> int:
        return self.services["api-ssl"].port


def _initial_states(device_services: Sequence[ServiceInfo] | None) -> dict[str, ServiceState]:
    if device_services:
        return {
            s.name: ServiceState(s.name, s.port, s.disabled, s.address) for s in device_services
        }
    return {
        name: ServiceState(name, port, name in OFFLINE_DEFAULT_DISABLED)
        for name, port in DEFAULT_SERVICE_PORTS.items()
    }


def _targets(command: Command, states: dict[str, ServiceState]) -> list[str] | None:
    """Service names the command applies to; None when it cannot be determined."""
    selector = command.selector
    if isinstance(selector, str):
        return [selector]
    if isinstance(selector, Find):
        if selector.raw is not None:
            return None
        # `dynamic=no` only leaves out connection rows, which are not services.
        conditions = [c for c in selector.conditions if c != NOT_DYNAMIC]
        if not conditions:
            return list(states)
        if len(conditions) == 1 and conditions[0][0] == "name":
            return [conditions[0][1]]
        return None
    return None


def _takes_connections_along(selector: object) -> bool:
    """A find in /ip service that also matches the services' open connections.

    7.20.7 lists every open connection as a dynamic row under its service's name, the
    console's find matches those rows too, and a change that includes one is refused:
    `set [find name=ssh] port=122` fails with "this is configured elsewhere" while an SSH
    session is open. `set ssh ...` and `[find name=ssh dynamic=no]` do not.
    """
    if not isinstance(selector, Find):
        return False
    if selector.raw is not None:
        return "dynamic=no" not in selector.raw.split()
    return NOT_DYNAMIC not in selector.conditions


def analyze_access(
    commands: Iterable[Command], device_services: Sequence[ServiceInfo] | None = None
) -> AccessReport:
    """The /ip service state once ``commands`` ran on a device that has ``device_services``,
    or on one without configuration (offline, or after a reset) when that is None."""
    states = _initial_states(device_services)
    issues: list[Issue] = []
    uncertain = False

    for command in commands:
        if command.path != SERVICE_MENU:
            continue
        if _takes_connections_along(command.selector):
            name = command.selector.get("name") if isinstance(command.selector, Find) else None
            issues.append(Issue("service.find_takes_connections", Severity.WARNING, _FIELD,
                                {"service": name or "ssh"}))  # the message's example
        targets = _targets(command, states)
        if targets is None:
            uncertain = True
            continue
        for name in targets:
            if name not in states:
                issues.append(Issue("service.unknown", Severity.WARNING, _FIELD, {"service": name}))
                continue
            state = states[name]
            if command.verb == "enable":
                state = replace(state, disabled=False)
            elif command.verb == "disable":
                state = replace(state, disabled=True)
            elif command.verb == "set":
                disabled = command.arg("disabled")
                if disabled is not None:
                    state = replace(state, disabled=disabled == "yes")
                port = command.arg("port")
                if port is not None:
                    if port.isdigit() and 1 <= int(port) <= 65535:
                        state = replace(state, port=int(port))
                    else:
                        issues.append(
                            Issue("service.port_invalid", Severity.ERROR, _FIELD,
                                  {"service": name, "port": port})
                        )
                address = command.arg("address")
                if address is not None:
                    state = replace(state, address=address)
            states[name] = state

    disabled_paths = tuple(n for n in MANAGEMENT_SERVICES if n in states and states[n].disabled)
    remaining = tuple(n for n in MANAGEMENT_SERVICES if n in states and not states[n].disabled)

    for name in disabled_paths:
        issues.append(Issue("access.path_disabled", Severity.WARNING, _FIELD, {"service": name}))
    if "api-ssl" in disabled_paths:
        issues.append(Issue("access.api_ssl_disabled", Severity.WARNING, _FIELD))
    if not remaining:
        issues.append(Issue("access.no_paths", Severity.ERROR, _FIELD))
    if uncertain:
        issues.append(Issue("access.uncertain", Severity.WARNING, _FIELD))

    by_port: dict[int, list[str]] = defaultdict(list)
    for state in states.values():
        if not state.disabled:
            by_port[state.port].append(state.name)
    for port, names in sorted(by_port.items()):
        if len(names) > 1:
            issues.append(
                Issue("service.port_collision", Severity.WARNING, _FIELD,
                      {"services": ", ".join(sorted(names)), "port": port})
            )

    for name in remaining:
        if states[name].address:
            issues.append(
                Issue("access.address_restricted", Severity.INFO, _FIELD,
                      {"service": name, "address": states[name].address})
            )

    return AccessReport(
        services=states,
        remaining=remaining,
        disabled_paths=disabled_paths,
        uncertain=uncertain,
        issues=tuple(issues),
    )


def analyze_access_text(
    text: str, device_services: Sequence[ServiceInfo] | None = None
) -> AccessReport:
    return analyze_access(parse(text).commands, device_services)
