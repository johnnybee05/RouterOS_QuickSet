"""Sections 5 and 6 from the editable hardening block.

The parsed block is split by menu: `/ip service` goes to section 6 (always last), the rest
(DNS, NTP, clock, SNMP, neighbor discovery, IPv6, logging, unknown menus) to section 5 in
their original order. Dialect translation happens later in ros6/ros7.
"""

from __future__ import annotations

from mqs.generator.hardening.parser import ParsedBlock
from mqs.generator.ir import Command, Comment, Find, Item, RawLine, Script, SectionKey, cmd
from mqs.issues import Issue, Severity
from mqs.model.profile import DeviceProfile

SERVICE_MENU = "/ip service"
LOGGING_MENU = "/system logging"
# Default /system logging rule order on a fresh device; NUTNO OVĚŘIT (docs/DIALECT_NOTES.md).
LOGGING_INDEX_TOPICS = {0: "info", 1: "error", 2: "warning", 3: "critical"}


def build(
    profile: DeviceProfile, script: Script, block: ParsedBlock, *, router_is_dns: bool
) -> list[Issue]:
    issues: list[Issue] = [p.to_issue() for p in block.problems]
    system = script.section(SectionKey.SYSTEM)
    services = script.section(SectionKey.SERVICES)

    if router_is_dns:
        system.add(
            Comment("LAN clients use the router as DNS server"),
            cmd("/ip dns", "set", allow_remote_requests=True),
        )
        issues.append(Issue("dns.remote_requests_enabled", Severity.INFO, "lan.dhcp_server"))
        if not profile.firewall.basic_filter:
            issues.append(Issue("dns.open_resolver", Severity.WARNING, "firewall.basic_filter"))

    system_items: list[Item] = []
    service_items: list[Item] = []
    for line in block.lines:
        item = line.item
        if item is None:
            continue
        robust = profile.services.robust_logging
        if robust and isinstance(item, Command) and item.path == LOGGING_MENU:
            item = _robust_logging(item, issues)
        (service_items if line.menu == SERVICE_MENU else system_items).append(item)

    if system_items:
        system.add(Comment("Hardening block"), *system_items)
    if service_items:
        services.add(*service_items)
    _service_extensions(profile, script)
    return issues


def _robust_logging(command: Command, issues: list[Issue]) -> Command | RawLine:
    if command.verb != "set" or not isinstance(command.selector, int):
        return command
    topics = LOGGING_INDEX_TOPICS.get(command.selector)
    if topics is None:
        issues.append(
            Issue("logging.robust_unknown_index", Severity.WARNING, "services.robust_logging",
                  {"index": command.selector})
        )
        return command
    return Command(command.path, command.verb, command.args, Find.by(topics=topics))


def _service_extensions(profile: DeviceProfile, script: Script) -> None:
    services = profile.services
    section = script.section(SectionKey.SERVICES)
    if services.api_ssl_certificate:
        section.add(
            Comment("api-ssl with an assigned certificate"),
            cmd(SERVICE_MENU, "set", selector="api-ssl", certificate=services.api_ssl_certificate),
        )
    if services.management_subnet is not None:
        subnet = str(services.management_subnet)
        section.add(
            Comment("Management access restricted to the management subnet"),
            *(cmd(SERVICE_MENU, "set", selector=name, address=subnet)
              for name in ("ssh", "winbox", "api-ssl")),
        )
