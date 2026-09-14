"""Section 4: firewall filter, FastTrack, NAT and MSS clamping."""

from __future__ import annotations

from mqs.generator.blocks.common import lan_match, wan_l3_interface, wan_match
from mqs.generator.context import TargetContext
from mqs.generator.ir import ArgValue, Comment, Script, SectionKey, cmd
from mqs.issues import Issue, Severity
from mqs.model.profile import DeviceProfile

FILTER = "/ip firewall filter"


def build(profile: DeviceProfile, ctx: TargetContext, script: Script) -> list[Issue]:
    section = script.section(SectionKey.FIREWALL)
    issues: list[Issue] = []
    firewall = profile.firewall
    has_wan = wan_l3_interface(profile) is not None

    if firewall.basic_filter:
        section.add(
            Comment("Firewall: input"),
            cmd(FILTER, "add", chain="input", action="accept",
                connection_state=["established", "related", "untracked"]),
            cmd(FILTER, "add", chain="input", action="drop", connection_state="invalid"),
            cmd(FILTER, "add", chain="input", action="accept", protocol="icmp"),
        )
        lan = lan_match(profile, ctx, "in")
        if lan:
            section.add(cmd(FILTER, "add", args={"chain": "input", "action": "accept", **lan}))
        if has_wan:
            section.add(
                cmd(FILTER, "add",
                    args={"chain": "input", "action": "drop", **wan_match(profile, ctx, "in")})
            )

    if firewall.basic_filter or firewall.fasttrack.enabled:
        section.add(Comment("Firewall: forward"))
    if firewall.fasttrack.enabled:
        fasttrack_args: dict[str, ArgValue] = {
            "chain": "forward",
            "action": "fasttrack-connection",
            "connection-state": ["established", "related"],
        }
        if firewall.fasttrack.hw_offload:
            if ctx.has("fasttrack_hw_offload"):
                fasttrack_args["hw-offload"] = True
            else:
                issues.append(
                    Issue("fasttrack.hw_offload_skipped", Severity.INFO,
                          "firewall.fasttrack.hw_offload")
                )
        # The accept rule must directly follow the fasttrack rule.
        section.add(
            cmd(FILTER, "add", args=fasttrack_args),
            cmd(FILTER, "add", chain="forward", action="accept",
                connection_state=["established", "related"]),
        )
    if firewall.basic_filter:
        if not firewall.fasttrack.enabled:
            section.add(
                cmd(FILTER, "add", chain="forward", action="accept",
                    connection_state=["established", "related"])
            )
        section.add(cmd(FILTER, "add", chain="forward", action="drop", connection_state="invalid"))
        if has_wan:
            section.add(
                cmd(FILTER, "add", args={
                    "chain": "forward",
                    "action": "drop",
                    "connection-state": "new",
                    "connection-nat-state": "!dstnat",
                    **wan_match(profile, ctx, "in"),
                })
            )

    if has_wan and profile.wan.nat_masquerade:
        section.add(
            Comment("NAT"),
            cmd("/ip firewall nat", "add",
                args={"chain": "srcnat", "action": "masquerade", **wan_match(profile, ctx, "out")}),
        )
    if has_wan and profile.wan.mss_clamping:
        section.add(
            Comment("MSS clamping (SYN packets are never fasttracked)"),
            cmd("/ip firewall mangle", "add", args={
                "chain": "forward",
                "action": "change-mss",
                "new-mss": "clamp-to-pmtu",
                "passthrough": True,
                "protocol": "tcp",
                "tcp-flags": "syn",
                **wan_match(profile, ctx, "out"),
            }),
        )
    return issues
