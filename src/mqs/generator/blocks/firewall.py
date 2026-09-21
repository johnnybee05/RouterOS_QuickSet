"""Section 4: firewall filter, FastTrack, NAT and MSS clamping.

Rules are evaluated top to bottom, so each chain is ordered like defconf: the most frequent
traffic (established connections) is accepted first, invalid packets are dropped, specific
accepts follow and the chain ends with a catch-all drop. Every rule carries a comment.
"""

from __future__ import annotations

from mqs.generator.blocks.common import lan_match, wan_l3_interface, wan_match
from mqs.generator.context import TargetContext
from mqs.generator.ir import ArgValue, Command, Comment, Script, SectionKey, cmd
from mqs.issues import Issue, Severity
from mqs.model.profile import DeviceProfile

FILTER = "/ip firewall filter"
NAT = "/ip firewall nat"
MANGLE = "/ip firewall mangle"
COMMENT_PREFIX = "quickset: "


def rule(path: str, chain: str, action: str, comment: str, **matchers: ArgValue) -> Command:
    """`add chain=… action=… comment="quickset: …" <matchers>`; matcher keys as in RouterOS."""
    return cmd(path, "add", args={"chain": chain, "action": action,
                                  "comment": COMMENT_PREFIX + comment, **matchers})


def negate(match: dict[str, str]) -> dict[str, str]:
    return {key: f"!{value}" for key, value in match.items()}


def build(profile: DeviceProfile, ctx: TargetContext, script: Script) -> list[Issue]:
    section = script.section(SectionKey.FIREWALL)
    issues: list[Issue] = []
    firewall = profile.firewall
    has_wan = wan_l3_interface(profile) is not None
    lan_in = lan_match(profile, ctx, "in")
    wan_in = wan_match(profile, ctx, "in")
    wan_out = wan_match(profile, ctx, "out")

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
            # Whitelist: an interface in neither list (a spare port, a new tunnel) is closed too.
            section.add(
                rule(FILTER, "input", "accept", "accept from LAN", **lan_in),
                rule(FILTER, "input", "drop", "drop all not coming from LAN", **negate(lan_in)),
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

    if has_wan and profile.wan.nat_masquerade:
        section.add(
            Comment("NAT"),
            rule(NAT, "srcnat", "masquerade", "masquerade to WAN", **wan_out),
        )
    if has_wan and profile.wan.mss_clamping:
        section.add(
            Comment("MSS clamping (SYN packets are never fasttracked)"),
            rule(MANGLE, "forward", "change-mss", "clamp MSS to path MTU", **{
                "new-mss": "clamp-to-pmtu",
                "passthrough": True,
                "protocol": "tcp",
                "tcp-flags": "syn",
                **wan_out,
            }),
        )
    return issues
