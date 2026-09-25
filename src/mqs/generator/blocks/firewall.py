"""Section 4: firewall filter, FastTrack, NAT and MSS clamping.

Rules are evaluated top to bottom, so each chain is ordered like defconf: the most frequent
traffic (established connections) is accepted first, invalid packets are dropped, specific
accepts follow and the chain ends with a catch-all drop. Every rule carries a comment.

Applied on top of a running configuration, `add` would append after the rules already
there, behind their drops. So the rules of an earlier run are removed by their comment
first, and the filter rules end up above the device's own ones.
"""

from __future__ import annotations

from mqs.generator.blocks.common import lan_match, wan_l3_interface, wan_match
from mqs.generator.context import TargetContext
from mqs.generator.hardening.access import AccessReport
from mqs.generator.ir import (
    ArgValue,
    Command,
    Comment,
    Find,
    KeepOwnFirst,
    Script,
    SectionKey,
    cmd,
)
from mqs.issues import Issue, Severity
from mqs.model.profile import DeviceProfile

FILTER = "/ip firewall filter"
NAT = "/ip firewall nat"
MANGLE = "/ip firewall mangle"
COMMENT_PREFIX = "quickset: "
OWN_REGEX = f"^{COMMENT_PREFIX}"  # the comments of the rules this block adds
OWN = f'comment~"{OWN_REGEX}"'
SNMP_PORT = 161
MNDP_PORT = 5678  # neighbor discovery, which Winbox also uses to list devices


def rule(path: str, chain: str, action: str, comment: str, **matchers: ArgValue) -> Command:
    """`add chain=… action=… comment="quickset: …" <matchers>`; matcher keys as in RouterOS."""
    return cmd(path, "add", args={"chain": chain, "action": action,
                                  "comment": COMMENT_PREFIX + comment, **matchers})


def remove_own(path: str) -> Command:
    """Remove the rules of an earlier run, so applying twice does not duplicate them."""
    return cmd(path, "remove", selector=Find.matching("comment", OWN_REGEX))


def keep_own_first(ctx: TargetContext) -> KeepOwnFirst:
    """The line that puts our filter rules above the device's own ones, in both orders.

    v7: `move` without a destination moves an item to the end, so the other rules are
    moved one by one and keep their order below ours; a no-op on an empty table, and
    dynamic rules (the fasttrack counter dummy) cannot be moved and are skipped. Verified
    on 7.20.7.

    v6: 6.49.10 cannot move an item that sits right above a builtin one ("failure:
    cannot move builtin", in the console and over the API alike), so a rule placed on
    top of the table, above the dummy, stopped the import at the v7 line. It does take
    that item as a destination, and ours, just added at the end, never sit above the
    dummy: they move as one block before the first other rule (v6 hEX S, 2026-09-25).
    Whether 7.x refuses the same move NUTNO OVĚŘIT.
    """
    others = f"[ {FILTER} find where dynamic=no and !({OWN}) ]"
    if ctx.major == 6:
        return KeepOwnFirst(
            f":if ([ :len {others} ] > 0) do={{ {FILTER} move [ find where dynamic=no and "
            f"{OWN} ] destination=[ :pick {others} 0 ] }}",
            menu=FILTER, key="comment", regex=OWN_REGEX, block=True)
    return KeepOwnFirst(f":foreach i in={others} do={{ {FILTER} move $i }}",
                        menu=FILTER, key="comment", regex=OWN_REGEX)


def negate(match: dict[str, str]) -> dict[str, str]:
    return {key: f"!{value}" for key, value in match.items()}


def wan_management(profile: DeviceProfile, script: Script, access: AccessReport,
                   wan_in: dict[str, str]) -> list[Command]:
    """Accept the management services from WAN, on the ports the script leaves them on.

    A service the script leaves disabled stays closed, and SNMP is opened only when the
    script enables it. With a management subnet only that subnet is accepted.
    """
    source: dict[str, str] = {}
    if profile.services.management_subnet is not None:
        source["src-address"] = str(profile.services.management_subnet)
    ports: list[tuple[str, str, int]] = []
    for name, label in (("api-ssl", "api-ssl"), ("ssh", "SSH"), ("winbox", "Winbox")):
        state = access.services.get(name)
        if state is not None and not state.disabled:
            ports.append((label, "tcp", state.port))
    if script.find("/snmp", "set", enabled="yes"):
        ports.append(("SNMP", "udp", SNMP_PORT))
    ports.append(("discovery", "udp", MNDP_PORT))
    return [
        rule(FILTER, "input", "accept", f"accept {label} from WAN",
             protocol=protocol, **{"dst-port": str(port)}, **wan_in, **source)
        for label, protocol, port in ports
    ]


def build(profile: DeviceProfile, ctx: TargetContext, script: Script,
          access: AccessReport) -> list[Issue]:
    section = script.section(SectionKey.FIREWALL)
    issues: list[Issue] = []
    firewall = profile.firewall
    has_wan = wan_l3_interface(profile) is not None
    lan_in = lan_match(profile, ctx, "in")
    wan_in = wan_match(profile, ctx, "in")
    wan_out = wan_match(profile, ctx, "out")
    filter_start = len(section.items)

    if firewall.basic_filter:
        section.add(
            Comment("Firewall: input (traffic to the router itself)"),
            rule(FILTER, "input", "accept", "accept established,related,untracked",
                 **{"connection-state": ["established", "related", "untracked"]}),
            rule(FILTER, "input", "drop", "drop invalid", **{"connection-state": "invalid"}),
            rule(FILTER, "input", "accept", "accept ICMP", protocol="icmp"),
            rule(FILTER, "input", "accept", "accept to local loopback",
                 **{"dst-address": "127.0.0.1"}),
        )
        if lan_in:
            section.add(rule(FILTER, "input", "accept", "accept from LAN", **lan_in))
        if has_wan and firewall.wan_management:
            section.add(*wan_management(profile, script, access, wan_in))
        if lan_in:
            # Whitelist: an interface in neither list (a spare port, a new tunnel) is closed too.
            section.add(
                rule(FILTER, "input", "drop", "drop all not coming from LAN", **negate(lan_in))
            )
        elif has_wan:
            section.add(rule(FILTER, "input", "drop", "drop all from WAN", **wan_in))

    if firewall.basic_filter or firewall.fasttrack.enabled:
        section.add(Comment("Firewall: forward (traffic through the router)"))
    if firewall.fasttrack.enabled:
        fasttrack_args: dict[str, ArgValue] = {"connection-state": ["established", "related"]}
        if firewall.fasttrack.hw_offload:
            if ctx.has("fasttrack_hw_offload"):
                fasttrack_args["hw-offload"] = True
            else:
                issues.append(
                    Issue("fasttrack.hw_offload_skipped", Severity.INFO,
                          "firewall.fasttrack.hw_offload")
                )
        # The fasttrack rule must be directly followed by the accept rule.
        section.add(rule(FILTER, "forward", "fasttrack-connection", "fasttrack",
                         **fasttrack_args))
    if firewall.basic_filter or firewall.fasttrack.enabled:
        section.add(
            rule(FILTER, "forward", "accept", "accept established,related,untracked",
                 **{"connection-state": ["established", "related", "untracked"]})
        )
    if firewall.basic_filter:
        section.add(
            rule(FILTER, "forward", "drop", "drop invalid", **{"connection-state": "invalid"})
        )
        if has_wan:
            section.add(
                rule(FILTER, "forward", "drop", "drop all from WAN not DSTNATed",
                     **{"connection-state": "new", "connection-nat-state": "!dstnat", **wan_in})
            )
    if len(section.items) > filter_start:
        section.items[filter_start:filter_start] = [
            Comment("Firewall: replace the rules of an earlier QuickSet run"),
            remove_own(FILTER),
        ]
        section.add(Comment("Firewall: keep the rules above those already on the device"),
                    keep_own_first(ctx))

    if has_wan and profile.wan.nat_masquerade:
        # Appended after the rules already there: a more specific src-nat stays first.
        section.add(
            Comment("NAT"),
            remove_own(NAT),
            rule(NAT, "srcnat", "masquerade", "masquerade to WAN", **wan_out),
        )
    if has_wan and profile.wan.mss_clamping:
        section.add(
            Comment("MSS clamping (SYN packets are never fasttracked)"),
            remove_own(MANGLE),
            rule(MANGLE, "forward", "change-mss", "clamp MSS to path MTU", **{
                "new-mss": "clamp-to-pmtu",
                "passthrough": True,
                "protocol": "tcp",
                "tcp-flags": "syn",
                **wan_out,
            }),
        )
    return issues
