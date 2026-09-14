"""RouterOS v6 dialect: translates the v7-authored hardening block (spec 4.4 / 4.5).

Never copies the block unchanged: NTP servers menu, IPv6 settings, SNMP ::/0 and
features newer than the target version are translated or dropped with an issue.
"""

from __future__ import annotations

import re

from mqs.generator.context import TargetContext
from mqs.generator.ir import Command, Comment, Item, RawLine, Script, SectionKey, cmd
from mqs.generator.render import render_inline
from mqs.issues import Issue, Severity
from mqs.model.inventory import Ipv6PackageState

_FIELD = "services.hardening_text"
NTP_CLIENT = "/system ntp client"
NTP_SERVERS = "/system ntp client servers"
_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")

# Hardening commands that need a feature; dropped when the target version lacks it.
_FEATURE_ARGS = {
    ("/ip neighbor discovery-settings", "discover-interface-list"): (
        "neighbor_discovery_interface_list"
    ),
}


class Dialect6:
    major = 6

    def translate(self, script: Script, ctx: TargetContext) -> list[Issue]:
        issues: list[Issue] = []
        for key in (SectionKey.SYSTEM, SectionKey.SERVICES):
            section = script.section(key)
            items = self._ntp(section.items, ctx, issues)
            section.items = [out for item in items for out in self._item(item, ctx, issues)]
        return issues

    # ---------------------------------------------------------------- NTP

    def _ntp(self, items: list[Item], ctx: TargetContext, issues: list[Issue]) -> list[Item]:
        servers: list[str] = []
        anchor: int | None = None
        client_set: Command | None = None
        result: list[Item] = []
        for item in items:
            path = item.path if isinstance(item, Command | RawLine) else None
            if path == NTP_SERVERS:
                if isinstance(item, Command) and item.verb == "add" and item.arg("address"):
                    servers.append(item.arg("address") or "")
                else:
                    issues.append(Issue("v6.ntp_dropped_line", Severity.WARNING, _FIELD,
                                        {"text": render_inline(item)}))  # type: ignore[arg-type]
                if anchor is None:
                    anchor = len(result)
                continue
            if isinstance(item, Command) and path == NTP_CLIENT and item.verb == "set":
                client_set = item if client_set is None else client_set.with_args(dict(item.args))
                if anchor is None:
                    anchor = len(result)
                continue
            result.append(item)

        if anchor is None:
            return result
        merged = client_set or cmd(NTP_CLIENT, "set", enabled=True)
        addresses = [s for s in servers if _IPV4_RE.match(s)]
        hostnames = [s for s in servers if not _IPV4_RE.match(s)]
        if addresses:
            if len(addresses) > 2:
                issues.append(Issue("v6.ntp_too_many", Severity.WARNING, _FIELD,
                                    {"dropped": ", ".join(addresses[2:])}))
            merged = merged.with_args(primary_ntp=addresses[0])
            if len(addresses) > 1:
                merged = merged.with_args(secondary_ntp=addresses[1])
        if hostnames:
            if ctx.has("ntp_server_dns_names"):
                merged = merged.with_args(server_dns_names=hostnames)
            else:
                for host in hostnames:
                    issues.append(Issue("v6.ntp_hostname_unsupported", Severity.WARNING, _FIELD,
                                        {"server": host}))
        result.insert(anchor, merged)
        return result

    # ---------------------------------------------------------------- other items

    def _item(self, item: Item, ctx: TargetContext, issues: list[Issue]) -> list[Item]:
        if isinstance(item, Comment):
            return [item]
        if isinstance(item, RawLine):
            issues.append(Issue("v6.raw_line_untranslated", Severity.WARNING, _FIELD,
                                {"text": render_inline(item)}))
            return [item]
        if item.path.startswith("/ipv6"):
            return self._ipv6(item, ctx, issues)
        if item.path == "/snmp community" and item.has_arg("addresses"):
            return [self._snmp(item, issues)]
        for (path, arg), feature in _FEATURE_ARGS.items():
            if item.path == path and item.has_arg(arg) and not ctx.has(feature):
                issues.append(Issue("v6.feature_missing", Severity.WARNING, _FIELD,
                                    {"version": str(ctx.version), "feature": arg,
                                     "text": render_inline(item)}))
                return []
        return [item]

    def _ipv6(self, item: Command, ctx: TargetContext, issues: list[Issue]) -> list[Item]:
        state = ctx.ipv6_package
        disables = item.path == "/ipv6 settings" and item.arg("disable-ipv6") == "yes"
        if state == Ipv6PackageState.ENABLED:
            if disables:
                issues.append(Issue("v6.ipv6_package_disable", Severity.WARNING, _FIELD))
                rest = item.without_args("disable-ipv6")
                translated: list[Item] = [rest] if rest.args else []
                return [*translated, cmd("/system package", "disable", selector="ipv6")]
            return [item]
        if disables:
            code = ("v6.ipv6_unknown_package" if state == Ipv6PackageState.UNKNOWN
                    else "v6.ipv6_omitted_missing")
            issues.append(Issue(code, Severity.WARNING if code.endswith("unknown_package")
                                else Severity.INFO, _FIELD))
        else:
            issues.append(Issue("v6.ipv6_dropped", Severity.WARNING, _FIELD,
                                {"text": render_inline(item)}))
        return []

    def _snmp(self, item: Command, issues: list[Issue]) -> Command:
        values = (item.arg("addresses") or "").split(",")
        kept: list[str] = []
        for value in values:
            if ":" not in value:
                kept.append(value)
            elif value == "::/0":
                kept.append("0.0.0.0/0")
            else:
                issues.append(Issue("v6.snmp_ipv6_address", Severity.WARNING, _FIELD,
                                    {"address": value}))
        return item.with_args(addresses=list(dict.fromkeys(kept)) or ["0.0.0.0/0"])
