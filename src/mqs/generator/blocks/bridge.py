"""Section 2: bridge, bridge ports, HW offload and interface lists."""

from __future__ import annotations

from mqs.capabilities.offload import OffloadPlan
from mqs.generator.blocks.common import (
    LAN_LIST,
    WAN_LIST,
    bridge_enabled,
    format_duration,
    uses_lan_list,
    uses_wan_list,
    wan_port,
)
from mqs.generator.context import TargetContext
from mqs.generator.ir import ArgValue, Comment, Find, Script, SectionKey, cmd
from mqs.issues import Issue, Severity
from mqs.model.bridge import ProtocolMode
from mqs.model.profile import DeviceProfile


def build(
    profile: DeviceProfile, ctx: TargetContext, script: Script, offload: OffloadPlan
) -> list[Issue]:
    section = script.section(SectionKey.INTERFACES)
    issues: list[Issue] = []
    bridge = profile.bridge

    if bridge_enabled(profile):
        if bridge.protocol_mode == ProtocolMode.MSTP and not ctx.has("bridge_mstp"):
            issues.append(
                Issue("bridge.mstp_unsupported_version", Severity.ERROR, "bridge.protocol_mode",
                      {"version": str(ctx.version)})
            )
        args: dict[str, ArgValue] = {"name": bridge.name, "protocol-mode": bridge.protocol_mode}
        if bridge.priority is not None:
            args["priority"] = f"0x{bridge.priority:04X}"
        if bridge.ageing_time_s is not None:
            args["ageing-time"] = format_duration(bridge.ageing_time_s)
        if bridge.vlan_filtering:
            args["vlan-filtering"] = True
            args["pvid"] = bridge.pvid
            if ctx.has("bridge_frame_types"):
                args["frame-types"] = bridge.frame_types
        section.add(Comment(f"Bridge {bridge.name}"), cmd("/interface bridge", "add", args=args))
        if bridge.use_ip_firewall:
            section.add(cmd("/interface bridge settings", "set", use_ip_firewall=True))

        for port in bridge.ports:
            port_args: dict[str, ArgValue] = {"bridge": bridge.name, "interface": port}
            if bridge.vlan_filtering:
                port_args["pvid"] = bridge.pvid
            section.add(cmd("/interface bridge port", "add", args=port_args))

        if offload.offered:
            hw_commands = [
                cmd("/interface bridge port", "set", selector=Find.by(interface=p.port), hw=p.hw)
                for p in offload.ports
                if p.hw is not None
            ]
            if hw_commands:
                section.add(Comment("Bridge HW offload"), *hw_commands)

    want_wan, want_lan = uses_wan_list(profile, ctx), uses_lan_list(profile, ctx)
    if want_wan or want_lan:
        section.add(Comment("Interface lists"))
        if want_wan:
            section.add(cmd("/interface list", "add", name=WAN_LIST))
        if want_lan:
            section.add(cmd("/interface list", "add", name=LAN_LIST))
        if want_wan:
            section.add(
                cmd("/interface list member", "add", interface=wan_port(profile), list=WAN_LIST)
            )
        if want_lan:
            section.add(
                cmd("/interface list member", "add", interface=bridge.name, list=LAN_LIST)
            )
    return issues
