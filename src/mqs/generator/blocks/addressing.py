"""Section 3: IP addresses, DHCP client / static WAN / PPPoE, pool and DHCP server."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Interface

from mqs.generator.blocks.common import (
    MASKED_SECRET,
    WAN_LIST,
    bridge_enabled,
    format_duration,
    uses_wan_list,
    wan_port,
)
from mqs.generator.context import TargetContext
from mqs.generator.hardening.binding import read_fields
from mqs.generator.ir import ArgValue, Comment, Script, SectionKey, cmd
from mqs.issues import Issue, Severity
from mqs.model.bridge import LanAddressing
from mqs.model.profile import DeviceProfile
from mqs.model.wan import DhcpWan, PppoeWan, StaticWan


def build(
    profile: DeviceProfile, ctx: TargetContext, script: Script, *, mask_secrets: bool = False
) -> list[Issue]:
    section = script.section(SectionKey.ADDRESSING)
    lan, bridge = profile.lan, profile.bridge
    issues: list[Issue] = []

    if bridge_enabled(profile):
        if lan.addressing == LanAddressing.STATIC and lan.address is not None:
            section.add(
                Comment("LAN address"),
                cmd("/ip address", "add", address=str(lan.address), interface=bridge.name),
            )
            if lan.gateway is not None:
                section.add(
                    Comment("Default route through the LAN gateway"),
                    cmd("/ip route", "add", dst_address="0.0.0.0/0", gateway=str(lan.gateway)),
                )
        elif lan.addressing == LanAddressing.DHCP_CLIENT:
            section.add(
                Comment("Management address from DHCP on the bridge"),
                cmd("/ip dhcp-client", "add", interface=bridge.name, disabled=False),
            )

    port = wan_port(profile)
    if port is not None:
        _wan(profile, ctx, script, port, mask_secrets)

    dhcp = lan.dhcp_server
    if (
        dhcp.enabled
        and bridge_enabled(profile)
        and lan.addressing == LanAddressing.STATIC
        and lan.address is not None
    ):
        gateway = dhcp.gateway or lan.address.ip
        network_args: dict[str, ArgValue] = {
            "address": str(lan.address.network),
            "gateway": str(gateway),
        }
        dns = dhcp_dns_servers(profile, lan.address)
        if dns:
            network_args["dns-server"] = dns
        else:
            issues.append(Issue("dhcp.no_dns_servers", Severity.WARNING,
                                "lan.dhcp_server.dns_servers"))
        if dhcp.comment:
            network_args["comment"] = dhcp.comment
        section.add(
            Comment("DHCP server"),
            cmd("/ip pool", "add", name=dhcp.pool_name, ranges=str(dhcp.pool)),
            cmd(
                "/ip dhcp-server",
                "add",
                name=dhcp.name,
                interface=bridge.name,
                address_pool=dhcp.pool_name,
                lease_time=format_duration(dhcp.lease_time_s),
                disabled=False,
            ),
            cmd("/ip dhcp-server network", "add", args=network_args),
        )
    return issues


def dhcp_dns_servers(profile: DeviceProfile, address: IPv4Interface) -> list[str]:
    """DNS servers the DHCP server hands out.

    A list given on the LAN page wins. Otherwise the bridge address when the router answers
    DNS itself (allow-remote-requests), else the servers the router asks: those of the
    hardening block, which runs last, or those of a static WAN. DHCP carries IPv4 only.
    """
    dhcp = profile.lan.dhcp_server
    if dhcp.dns_servers:
        return [str(d) for d in dhcp.dns_servers]
    if profile.router_is_dns:
        return [str(address.ip)]
    servers = read_fields(profile.services.hardening_text).dns_servers
    if servers is None:
        settings, servers = profile.wan.settings, ()
        if wan_port(profile) is not None and isinstance(settings, StaticWan):
            servers = tuple(str(s) for s in settings.dns_servers)
    return [s for s in servers if _is_ipv4(s)]


def _is_ipv4(text: str) -> bool:
    try:
        IPv4Address(text)
    except ValueError:
        return False
    return True


def _wan(
    profile: DeviceProfile, ctx: TargetContext, script: Script, port: str, mask_secrets: bool
) -> None:
    section = script.section(SectionKey.ADDRESSING)
    settings = profile.wan.settings
    if isinstance(settings, DhcpWan):
        section.add(
            Comment(f"WAN: DHCP client on {port}"),
            cmd(
                "/ip dhcp-client",
                "add",
                interface=port,
                use_peer_dns=settings.use_peer_dns,
                add_default_route=settings.add_default_route,
                disabled=False,
            ),
        )
    elif isinstance(settings, StaticWan):
        section.add(
            Comment(f"WAN: static address on {port}"),
            cmd("/ip address", "add", address=str(settings.address), interface=port),
            cmd("/ip route", "add", dst_address="0.0.0.0/0", gateway=str(settings.gateway)),
        )
        if settings.dns_servers:
            script.section(SectionKey.SYSTEM).add(
                Comment("DNS servers from static WAN"),
                cmd("/ip dns", "set", servers=[str(s) for s in settings.dns_servers]),
            )
    elif isinstance(settings, PppoeWan):
        password = MASKED_SECRET if mask_secrets else settings.password.get_secret_value()
        section.add(
            Comment(f"WAN: PPPoE client on {port}"),
            cmd(
                "/interface pppoe-client",
                "add",
                name=settings.interface_name,
                interface=port,
                user=settings.username,
                password=password,
                use_peer_dns=settings.use_peer_dns,
                add_default_route=settings.add_default_route,
                disabled=False,
            ),
        )
        if uses_wan_list(profile, ctx):
            section.add(
                cmd("/interface list member", "add", interface=settings.interface_name,
                    list=WAN_LIST)
            )
