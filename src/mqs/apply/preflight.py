"""Checks before anything is changed on the device."""

from __future__ import annotations

from mqs.apply.adopt import ExistingObjects, adopt_existing, collect_existing
from mqs.apply.plan import MIN_ROLLBACK_MINUTES, ApplyMode, ApplyOptions, result_for_mode
from mqs.generator.base import GenerationResult
from mqs.generator.blocks.common import wan_l3_interface, wan_port
from mqs.issues import Issue, Severity, has_errors
from mqs.model.inventory import DeviceInventory
from mqs.model.profile import DeviceProfile

__all__ = ["ExistingObjects", "collect_existing", "preflight", "prepare_import"]

E, W, INFO = Severity.ERROR, Severity.WARNING, Severity.INFO
_MANAGEMENT = ("api-ssl", "ssh", "winbox")


def preflight(
    profile: DeviceProfile,
    result: GenerationResult,
    validation: list[Issue],
    options: ApplyOptions,
    inventory: DeviceInventory | None,
    *,
    existing: ExistingObjects | None = None,
    session_port: str | None = None,
) -> list[Issue]:
    issues: list[Issue] = []
    result = result_for_mode(result, options.mode)
    if has_errors([*validation, *result.issues]):
        issues.append(Issue("apply.blocked_by_errors", E))
    if options.mode == ApplyMode.GENERATE_ONLY:
        return issues
    if inventory is None:
        return [*issues, Issue("apply.not_connected", E)]

    device, target = inventory.version, result.target
    if device.major != target.major:
        if options.confirm_dialect_mismatch:
            issues.append(Issue("apply.dialect_mismatch", W, "",
                                {"device": str(device), "target": str(target)}))
        else:
            issues.append(Issue("apply.dialect_mismatch_unconfirmed", E, "",
                                {"device": str(device), "target": str(target)}))
    elif device != target:
        issues.append(Issue("apply.target_version_differs", INFO, "",
                            {"device": str(device), "target": str(target)}))

    if options.mode == ApplyMode.RESET_AND_IMPORT:
        if options.confirmed_identity != inventory.identity:
            issues.append(Issue("apply.reset_identity_mismatch", E, "",
                                {"identity": inventory.identity}))
        if not options.admin_password:
            issues.append(Issue("apply.reset_password_required", E))
        if options.rollback_minutes is not None:
            issues.append(Issue("apply.rollback_ignored_reset", W))
    elif options.rollback_minutes is not None and options.rollback_minutes < MIN_ROLLBACK_MINUTES:
        issues.append(Issue("apply.rollback_too_short", E, "",
                            {"minimum": MIN_ROLLBACK_MINUTES}))

    if session_port is not None:
        if session_port == wan_port(profile):
            issues.append(_wan_session(profile, result, session_port))
        elif (options.mode == ApplyMode.RESET_AND_IMPORT
              and session_port not in profile.bridge.ports):
            issues.append(Issue("apply.lockout_unconfigured_port", W, "bridge.ports",
                                {"port": session_port}))

    if options.mode == ApplyMode.IMPORT:
        issues.extend(adopt_existing(result, existing)[1])

    issues.extend(_notices(result))
    return issues


def prepare_import(result: GenerationResult, options: ApplyOptions,
                   existing: ExistingObjects | None) -> GenerationResult:
    """The script to show and send: on a running configuration, adopted to what is there."""
    if options.mode != ApplyMode.IMPORT:
        return result
    return adopt_existing(result, existing)[0]


def _wan_session(profile: DeviceProfile, result: GenerationResult, port: str) -> Issue:
    """What becomes of a session that arrives on the port the profile turns into WAN."""
    if wan_l3_interface(profile) != port:  # PPPoE: the port itself keeps no IP address
        return Issue("apply.lockout_wan_pppoe", W, "wan.interface", {"port": port})
    firewall = profile.firewall
    if firewall.basic_filter and not firewall.wan_management:
        return Issue("apply.lockout_wan", W, "firewall.wan_management", {"port": port})
    # The filter is off, or it lets the management services in from WAN.
    paths = ", ".join(f"{n} {result.access.services[n].port}" for n in result.access.remaining)
    return Issue("apply.session_on_wan", INFO, "wan.interface", {"port": port, "paths": paths})


def _notices(result: GenerationResult) -> list[Issue]:
    """Warnings the GUI must show before applying (spec 4.5)."""
    access = result.access
    services = access.services
    notices = [Issue("apply.notice_ssh_port", INFO, "", {"port": access.ssh_port})]
    disabled = sorted(n for n, s in services.items() if s.disabled and n not in _MANAGEMENT)
    if disabled:
        notices.append(Issue("apply.notice_services_disabled", INFO, "",
                             {"services": ", ".join(disabled)}))
    if "api-ssl" in access.remaining:
        notices.append(Issue("apply.notice_api_ssl_kept", INFO, "", {"port": access.api_ssl_port}))
    if result.target.major >= 7 and services.get("www-ssl") and services["www-ssl"].disabled:
        notices.append(Issue("apply.notice_rest_api", INFO))
    paths = ", ".join(f"{n} {services[n].port}" for n in access.remaining)
    notices.append(Issue("apply.notice_remaining_paths", INFO, "",
                         {"count": len(access.remaining), "paths": paths or "-"}))
    return notices
