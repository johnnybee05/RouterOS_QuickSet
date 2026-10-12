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
DNS_MENU = "/ip dns"
REMOTE_REQUESTS = "allow-remote-requests"
LOGGING_MENU = "/system logging"
IPV6_SETTINGS = "/ipv6 settings"
DISABLE_IPV6 = "disable-ipv6"
# Default /system logging rule order on a fresh device; NUTNO OVĚŘIT (docs/DIALECT_NOTES.md).
LOGGING_INDEX_TOPICS = {0: "info", 1: "error", 2: "warning", 3: "critical"}


def build(profile: DeviceProfile, script: Script, block: ParsedBlock) -> list[Issue]:
    issues: list[Issue] = [p.to_issue() for p in block.problems]
    system = script.section(SectionKey.SYSTEM)
    services = script.section(SectionKey.SERVICES)

    # Always explicit: an import onto a router would otherwise keep a resolver left on.
    remote = profile.router_is_dns
    system.add(
        Comment("DNS: the router answers LAN clients" if remote
                else "DNS: the router answers no remote requests"),
        cmd(DNS_MENU, "set", args={REMOTE_REQUESTS: remote}),
    )
    issues += _remote_requests_in_block(block, remote)

    system_items: list[Item] = []
    service_items: list[Item] = []
    for line in block.lines:
        item = line.item
        if item is None:
            continue
        robust = profile.services.robust_logging
        if robust and isinstance(item, Command) and item.path == LOGGING_MENU:
            item = _robust_logging(item, issues)
        if profile.firewall.ipv6 and isinstance(item, Command) and item.path == IPV6_SETTINGS:
            item = _keep_ipv6_on(item, issues)
            if item is None:
                continue
        (service_items if line.menu == SERVICE_MENU else system_items).append(item)

    if system_items:
        system.add(Comment("Hardening block"), *system_items)
    if service_items:
        services.add(*service_items)
    _service_extensions(profile, script)
    return issues


def _remote_requests_in_block(block: ParsedBlock, remote: bool) -> list[Issue]:
    """The block runs after the line above, so its own allow-remote-requests wins."""
    issues = []
    for line in block.lines:
        item = line.item
        if not (isinstance(item, Command) and item.path == DNS_MENU and item.verb == "set"):
            continue
        value = item.arg(REMOTE_REQUESTS)
        if value is not None and (value in ("yes", "true")) != remote:
            issues.append(Issue("dns.remote_requests_in_hardening", Severity.WARNING,
                                "services.hardening_text", {"value": value}))
    return issues


def _keep_ipv6_on(command: Command, issues: list[Issue]) -> Command | None:
    """IPv6 switched on in the firewall settings wins over `disable-ipv6` in the block.

    The firewall section (4) turns IPv6 on before its IPv6 rules, and section 5 runs after
    it, so the block's `disable-ipv6=yes` would switch it off again: the argument goes.
    """
    value = command.arg(DISABLE_IPV6)
    if value is None:
        return command
    if value in ("yes", "true"):
        issues.append(Issue("firewall.ipv6_overrides_hardening", Severity.INFO, "firewall.ipv6"))
    rest = command.without_args(DISABLE_IPV6)
    return rest if rest.args else None


def _robust_logging(command: Command, issues: list[Issue]) -> Command | RawLine:
    """`set 0 …` → `set [ find where topics~"^info$" ] …`.

    `topics` is an array. On 7.20.7 `find topics=info` matched no rule at all, so the
    `set` did nothing and said nothing, while `find topics="info"` and the regex matched
    the one rule (verified 2026-09-23). The regex is what the API transport can evaluate.
    """
    if command.verb != "set" or not isinstance(command.selector, int):
        return command
    topics = LOGGING_INDEX_TOPICS.get(command.selector)
    if topics is None:
        issues.append(
            Issue("logging.robust_unknown_index", Severity.WARNING, "services.robust_logging",
                  {"index": command.selector})
        )
        return command
    return Command(command.path, command.verb, command.args,
                   Find.matching("topics", f"^{topics}$"))


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
