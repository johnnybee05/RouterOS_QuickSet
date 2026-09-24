"""Generator entry point: blocks in the mandatory order, then the dialect pass."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from mqs import __version__
from mqs.capabilities.matrix import Capabilities
from mqs.capabilities.offload import OffloadPlan, plan_offload, plan_offload_without_hardware
from mqs.generator.blocks import addressing, bridge, firewall, identity, system
from mqs.generator.blocks.common import bridge_enabled
from mqs.generator.context import TargetContext
from mqs.generator.features import is_supported_version
from mqs.generator.hardening.access import AccessReport, analyze_access
from mqs.generator.hardening.binding import read_fields
from mqs.generator.hardening.parser import parse
from mqs.generator.ir import Script, SectionKey
from mqs.generator.render import render_script
from mqs.generator.ros6 import Dialect6
from mqs.generator.ros7 import Dialect7
from mqs.issues import Issue, Severity, has_errors
from mqs.model.bridge import LanAddressing
from mqs.model.ports import port_renames
from mqs.model.profile import DeviceProfile
from mqs.model.version import RosVersion
from mqs.model.wan import StaticWan


@dataclass(frozen=True)
class GenerationResult:
    text: str
    script: Script
    issues: tuple[Issue, ...]
    target: RosVersion
    offload: OffloadPlan
    access: AccessReport
    # Device interface name now -> name once the script has run, for the ports it renames.
    # What is read from the device before the apply is in the old names.
    renames: Mapping[str, str] = field(default_factory=dict)

    @property
    def dialect(self) -> int:
        return self.target.major

    @property
    def has_errors(self) -> bool:
        return has_errors(self.issues)


def router_is_dns(profile: DeviceProfile) -> bool:
    lan, dhcp = profile.lan, profile.lan.dhcp_server
    if not (dhcp.enabled and bridge_enabled(profile) and lan.addressing == LanAddressing.STATIC
            and lan.address is not None):
        return False
    gateway = dhcp.gateway or lan.address.ip
    return lan.address.ip in (dhcp.dns_servers or [gateway])


def _header(profile: DeviceProfile, ctx: TargetContext, now: datetime | None) -> str:
    lines = [
        f"MikroTik QuickSet Plus {__version__}",
        f"Target: RouterOS {ctx.version} (v{ctx.major} dialect)",
        f"Identity: {profile.identity}",
    ]
    if now is not None:
        lines.append(f"Generated: {now:%Y-%m-%d %H:%M}")
    return "\n".join(lines)


def generate(
    profile: DeviceProfile,
    ctx: TargetContext,
    *,
    capabilities: Capabilities | None = None,
    header: bool = True,
    now: datetime | None = None,
    mask_secrets: bool = False,
) -> GenerationResult:
    issues: list[Issue] = []
    if not is_supported_version(ctx.version):
        issues.append(Issue("target.unsupported_version", Severity.ERROR, "",
                            {"version": str(ctx.version)}))

    inventory = ctx.inventory
    renames = port_renames(profile.interface_names, inventory) if inventory is not None else {}
    if capabilities is None and inventory is not None:
        capabilities = Capabilities.from_inventory(inventory.renamed(renames))
    if capabilities is not None:
        offload = plan_offload(profile.bridge, capabilities)
    else:
        offload = plan_offload_without_hardware(profile.bridge, ctx.version)
    issues.extend(offload.issues)

    script = Script()
    for key in SectionKey:
        script.section(key)
    issues += identity.build(profile, ctx, script)
    issues += bridge.build(profile, ctx, script, offload)
    issues += addressing.build(profile, ctx, script, mask_secrets=mask_secrets)
    block = parse(profile.services.hardening_text)
    issues += system.build(profile, script, block, router_is_dns=router_is_dns(profile))

    dialect = Dialect6() if ctx.major == 6 else Dialect7()
    issues += dialect.translate(script, ctx)

    device_services = ctx.inventory.services if ctx.inventory is not None else None
    access = analyze_access(script.section(SectionKey.SERVICES).commands(), device_services)
    issues.extend(access.issues)
    # Built last because it opens the final service ports; its section still renders 4th.
    issues += firewall.build(profile, ctx, script, access)

    wan_settings = profile.wan.settings
    if (profile.wan.enabled and isinstance(wan_settings, StaticWan) and wan_settings.dns_servers
            and read_fields(profile.services.hardening_text).dns_servers is not None):
        issues.append(Issue("wan.static.dns_overridden", Severity.INFO,
                            "wan.settings.dns_servers"))

    unverified = ctx.unverified_consulted()
    if unverified:
        issues.append(Issue("feature.unverified", Severity.INFO, "",
                            {"features": ", ".join(f.key for f in unverified)}))

    text = render_script(script, header=_header(profile, ctx, now) if header else None)
    return GenerationResult(
        text=text,
        script=script,
        issues=tuple(issues),
        target=ctx.version,
        offload=offload,
        access=access,
        renames=renames,
    )
