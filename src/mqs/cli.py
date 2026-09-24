"""Developer CLI for the device side: look around, and run the whole apply flow.

The window and this command share `mqs.apply.session`, so an apply driven from here is
the one the window performs. That is the point of it: the flow can be exercised end to
end, against real hardware, without a GUI in the way.

Passwords are never command-line arguments. They come from an environment variable or a
prompt, so they stay out of the shell history and out of the process list.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any

from mqs.apply.orchestrator import ApplyAborted, ApplyEvent, ApplyOrchestrator, ApplyReport
from mqs.apply.plan import (
    DEFAULT_KEEP_BACKUPS,
    ApplyMode,
    ApplyOptions,
    build_upload_script,
    make_names,
)
from mqs.apply.preflight import collect_existing, preflight, prepare_import
from mqs.apply.session import (
    DeviceSession,
    TempAddressLeft,
    build_apply_context,
    end_session,
    open_mac_session,
    open_session,
    probe_summary,
    session_after_apply,
)
from mqs.apply.update import plan_from_dir
from mqs.generator.base import generate
from mqs.generator.context import TargetContext
from mqs.issues import Issue, Severity
from mqs.messages_cs import STEP_PREFIX
from mqs.model.ports import adopt_device_names
from mqs.model.profile import DeviceProfile
from mqs.model.validation import validate_profile
from mqs.paths import profiles_dir, update_dir
from mqs.profiles.store import ProfileStore, import_profile
from mqs.transport.base import AuthError, ConnectionParams, TransportError, TransportKind
from mqs.transport.factory import TransportChoice
from mqs.transport.mac_connect import TEMP_COMMENT, local_interface_for
from mqs.transport.mndp import DiscoveredDevice, MndpScanner
from mqs.transport.netifs import LocalInterface, list_ipv4_interfaces

OK = 0
FAILED = 1
BLOCKED = 2  # preflight said no; the device was not touched
REFUSED = 3  # the change was not confirmed


def _password(env_name: str, prompt: str, *, ask: bool = True) -> str:
    """From the environment, else asked for interactively, never from the command line.

    An empty environment variable counts as an answer, which is how a key-only or
    unattended run says "there is no password".
    """
    value = os.environ.get(env_name)
    if value is not None:
        return value
    return getpass.getpass(prompt) if ask and sys.stdin.isatty() else ""


class CliError(Exception):
    """A problem the user can fix on the command line; reported without a traceback."""


def connection_params(args: argparse.Namespace) -> ConnectionParams:
    return ConnectionParams(
        host=args.host or "",
        username=args.user,
        # A key is credential enough: do not stop an unattended run to ask for nothing.
        # MAC-Telnet has no keys, so a MAC connection always needs the password.
        password=_password(args.password_env,
                           f"Password for {args.user}@{args.host or args.mac}: ",
                           ask=not args.key or bool(args.mac)),
        key_filename=args.key,
        ca_file=args.ca_file,
    )


def load_profile(args: argparse.Namespace) -> DeviceProfile:
    if args.profile_file:
        return import_profile(Path(args.profile_file))
    return ProfileStore(profiles_dir()).load(args.profile)


def describe_session(session: DeviceSession, out: Any) -> None:
    inventory = session.inventory
    board = inventory.routerboard
    print(f"transport   : {session.transport.kind.value}:{session.transport.port}"
          f"{'  (fell back from api-ssl)' if session.fell_back else ''}", file=out)
    print(f"identity    : {inventory.identity}", file=out)
    print(f"version     : {inventory.version}", file=out)
    # The model is the hardware identifier; board-name is a marketing string shared by
    # different boards, so both are shown and only the model is ever matched on.
    print(f"board       : model {board.model if board else '?'}, "
          f"board-name {inventory.resource.board_name or '?'}, "
          f"arch {inventory.resource.architecture or '?'}", file=out)
    print(f"flash dir   : {'yes' if inventory.has_flash_dir else 'no'}", file=out)
    defaults = {i.name: i.default_name for i in inventory.interfaces
                if i.default_name and i.default_name != i.name}
    print("ports       : " + ", ".join(f"{p} ({defaults[p]})" if p in defaults else p
                                       for p in inventory.port_names), file=out)
    print(f"switches    : {', '.join(s.type for s in inventory.switches) or '-'}", file=out)
    print(f"services    : {', '.join(f'{s.name}:{s.port}' for s in inventory.services)}", file=out)
    print(f"session port: {session.session_port or '?'}", file=out)
    route = session.reached_by_mac
    if route is not None:
        print(f"by MAC      : {route.mac} over {session.mac_method}, "
              f"{session.transport.kind.value} on {session.params.host}, "
              f"temporary address {session.temp_address or '-'}", file=out)
    print(f"probes      : {probe_summary(session.probes) or '-'}", file=out)


def print_issues(issues: Sequence[Issue], out: Any) -> None:
    for issue in issues:
        print(f"  {issue.severity.value:7} {issue.code}: {issue.message}", file=out)


def build_options(args: argparse.Namespace, mode: ApplyMode) -> ApplyOptions:
    reset = mode == ApplyMode.RESET_AND_IMPORT
    keep_users = reset and args.keep_users
    return ApplyOptions(
        mode=mode,
        rollback_minutes=args.rollback_minutes if mode == ApplyMode.IMPORT else None,
        reset_delay_s=args.reset_delay,
        # With keep-users admin keeps its password unless the variable gives a new one.
        admin_password=_password(args.admin_password_env, "New admin password after reset: ",
                                 ask=not keep_users) if reset else "",
        keep_users=keep_users,
        download_dir=Path(args.download_dir) if args.download_dir else None,
        confirm_dialect_mismatch=args.allow_dialect_mismatch,
        confirmed_identity=args.confirm_identity or "",
        keep_backups=args.keep_backups,
    )


def confirmed(args: argparse.Namespace, options: ApplyOptions, session: DeviceSession) -> bool:
    if args.yes:
        return True
    if not sys.stdin.isatty():
        print("refusing to change the device: pass --yes", file=sys.stderr)
        return False
    try:
        answer = input(f"{options.mode.value} on {session.inventory.identity}"
                       f" ({session.params.host}) — type yes: ")
    except EOFError:  # redirected stdin that still claims to be a terminal
        return False
    return answer.strip().lower() == "yes"


# ------------------------------------------------------------------ commands


def cmd_discover(args: argparse.Namespace, out: Any, **_: Any) -> int:
    scanner = MndpScanner(list_ipv4_interfaces())
    scanner.open()
    if not scanner.listening:
        print("no interface to listen on: " + "; ".join(scanner.errors), file=sys.stderr)
        return FAILED
    scanner.solicit()
    deadline = args.seconds
    while deadline > 0:
        scanner.poll(1.0)
        deadline -= 1
    devices = scanner.devices()
    for device in devices:
        print(f"{device.mac}  {device.ipv4 or '-'!s:15}  {device.identity:20}  "
              f"{device.version:10}  {device.board}  via {device.local_interface}", file=out)
    for error in scanner.errors:
        print(f"  ! {error}", file=sys.stderr)
    return OK if devices else FAILED


def find_by_mac(mac: str, seconds: int) -> DiscoveredDevice | None:
    """The device announcing ``mac`` over MNDP, or None when it stays silent."""
    wanted = mac.replace("-", ":").upper()
    with MndpScanner(list_ipv4_interfaces()) as scanner:
        scanner.solicit()
        for _ in range(max(1, seconds)):
            scanner.poll(1.0)
            match = next((d for d in scanner.devices() if d.mac.upper() == wanted), None)
            if match is not None:
                return match
    return None


def open_device(
    args: argparse.Namespace,
    out: Any,
    *,
    connect: Callable[..., DeviceSession] = open_session,
    connect_mac: Callable[..., DeviceSession] = open_mac_session,
    find_device: Callable[[str, int], DiscoveredDevice | None] = find_by_mac,
    interfaces: Callable[[], list[LocalInterface]] = list_ipv4_interfaces,
) -> DeviceSession:
    """Connect by address, or by MAC the way the window's "Připojit přes MAC" does."""

    def on_step(step: object) -> None:
        print(f"  … {step}", file=out, flush=True)

    params = connection_params(args)
    choice = TransportChoice(args.transport)
    if not args.mac:
        return connect(params, choice, api_port=args.api_port, ssh_port=args.ssh_port,
                       on_step=on_step)
    device = find_device(args.mac, args.scan_seconds)
    if device is None:
        raise CliError(f"{args.mac} did not announce itself over MNDP within "
                       f"{args.scan_seconds} s")
    interface = local_interface_for(device, interfaces())
    if interface is None:
        raise CliError(f"no network card of this PC is on the network {args.mac} was heard on")
    try:
        address = IPv4Address(args.temp_address)
    except ValueError as exc:
        raise CliError(f"--temp-address: {exc}") from exc
    if address not in interface.network or address == interface.address:
        raise CliError(f"--temp-address {address} is not a free address in "
                       f"{interface.network} ({interface.name})")
    on_step(f"lending {address}/{interface.network.prefixlen} to "
            f"{device.identity or device.mac} on {device.interface_name or '?'}")
    return connect_mac(device, params, address, interface, choice,
                       api_port=args.api_port, ssh_port=args.ssh_port, on_step=on_step)


def hang_up(session: DeviceSession, out: Any) -> None:
    """Close the session, giving back the address lent to reach the device by MAC."""
    address = session.temp_address
    try:
        end_session(session)
    except TempAddressLeft as exc:
        print(f"warning: {exc}; remove it by hand (comment {TEMP_COMMENT})", file=sys.stderr)
        return
    if address is not None:
        print(f"  … temporary address {address} removed", file=out)


def cmd_connect(args: argparse.Namespace, out: Any, **deps: Any) -> int:
    session = open_device(args, out, **deps)
    try:
        describe_session(session, out)
    finally:
        hang_up(session, out)
    return OK


def _generate_offline(args: argparse.Namespace, out: Any) -> int:
    profile = load_profile(args)
    result = generate(profile, TargetContext.offline(args.target))
    issues = [*validate_profile(profile), *result.issues]
    if args.out:
        Path(args.out).write_text(result.text, encoding="utf-8", newline="\n")
        print(f"written to {args.out}", file=out)
    else:
        print(result.text, file=out)
    print_issues(issues, out)
    return BLOCKED if any(i.severity == Severity.ERROR for i in issues) else OK


def cmd_apply(
    args: argparse.Namespace,
    out: Any,
    *,
    collect_existing_fn: Callable[..., Any] = collect_existing,
    orchestrator_factory: Callable[..., Any] = ApplyOrchestrator,
    now: Callable[[], datetime] = datetime.now,
    **deps: Any,
) -> int:
    mode = ApplyMode(args.mode)
    if mode == ApplyMode.GENERATE_ONLY and not args.host and not args.mac:
        return _generate_offline(args, out)

    profile = load_profile(args)
    options = build_options(args, mode)
    session = open_device(args, out, **deps)
    try:
        return _apply_to(session, profile, options, args, out,
                         collect_existing_fn=collect_existing_fn,
                         orchestrator_factory=orchestrator_factory, now=now)
    finally:
        hang_up(session, out)


def _apply_to(
    session: DeviceSession,
    profile: DeviceProfile,
    options: ApplyOptions,
    args: argparse.Namespace,
    out: Any,
    *,
    collect_existing_fn: Callable[..., Any],
    orchestrator_factory: Callable[..., Any],
    now: Callable[[], datetime],
) -> int:
    mode = options.mode
    describe_session(session, out)
    if args.update and mode == ApplyMode.RESET_AND_IMPORT:
        directory = Path(args.update_dir) if args.update_dir else update_dir()
        options = replace(options, update=plan_from_dir(directory, session.inventory))

    # What the window does on connect: the device's port names go into the profile.
    adopted = adopt_device_names(profile, session.inventory)
    if adopted is not profile:
        renamed = ", ".join(f"{d}={n}" for d, n in adopted.interface_names.items()
                            if profile.interface_names.get(d) != n)
        print(f"port names  : taken over from the device: {renamed or '-'}", file=out)
        profile = adopted
    context = TargetContext.from_inventory(session.inventory, session.probes)
    if options.update_target is not None:
        context = context.after_update(options.update_target)
    result = generate(profile, context)
    existing = collect_existing_fn(session.transport) if mode != ApplyMode.GENERATE_ONLY else None
    issues = preflight(profile, result, validate_profile(profile, session.inventory), options,
                       session.inventory, existing=existing, session_port=session.session_port)
    print("\npreflight:", file=out)
    print_issues(issues, out)
    if any(i.severity == Severity.ERROR for i in issues):
        return BLOCKED
    result = prepare_import(result, options, existing)

    names = make_names(now(), session.inventory.has_flash_dir)
    if args.show_script:
        print("\nscript to upload (secrets masked):", file=out)
        print(build_upload_script(result.text, options, names, mask_secrets=True), file=out)
    if mode == ApplyMode.GENERATE_ONLY:
        return OK
    if not confirmed(args, options, session):
        return REFUSED

    print(f"\napplying in mode {mode.value}:", file=out)

    def on_event(event: ApplyEvent) -> None:
        line = f"[{STEP_PREFIX[event.status]}] {event.message}"
        if args.verbose and event.detail:
            line += f"   ({event.detail})"
        print(line, file=out, flush=True)

    ctx = build_apply_context(profile, result, options, session, on_event=on_event, now=now)
    try:
        report: ApplyReport = orchestrator_factory(ctx).run()
    except ApplyAborted as exc:
        session_after_apply(session, exc.report)
        print(f"aborted: {exc}", file=sys.stderr)
        return FAILED
    except TransportError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return FAILED
    session_after_apply(session, report)

    print("\nchecklist:", file=out)
    for check in report.checks:
        print(f"  {'OK  ' if check.ok else 'FAIL'} {check.message}", file=out)
    print_issues(report.issues, out)
    if report.downloaded:
        print(f"downloaded: {', '.join(str(p) for p in report.downloaded)}", file=out)
    return OK if report.succeeded else FAILED


# ------------------------------------------------------------------ parser


def _add_connection_args(parser: argparse.ArgumentParser, *, host_required: bool) -> None:
    parser.add_argument("--host", required=host_required, help="device address")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password-env", default="MQS_PASSWORD",
                        metavar="VAR", help="environment variable holding the password")
    parser.add_argument("--key", help="SSH private key file")
    parser.add_argument("--ca-file", help="CA bundle for api-ssl")
    parser.add_argument("--transport", default=TransportChoice.AUTO.value,
                        choices=[c.value for c in TransportChoice])
    parser.add_argument("--api-port", type=int)
    parser.add_argument("--ssh-port", type=int)
    parser.add_argument("--mac", help="reach the device by MAC address instead, as the "
                        "window's 'Připojit přes MAC' does; files then go over MAC-Telnet")
    parser.add_argument("--temp-address", metavar="IP",
                        help="with --mac: a free address in this PC's subnet to lend the "
                        "device while connected (the window proposes one and asks)")
    parser.add_argument("--scan-seconds", type=int, default=10,
                        help="with --mac: how long to wait for the device's MNDP announcement")


def build_parser() -> argparse.ArgumentParser:
    # No abbreviation: "--admin-password" would otherwise be accepted as a short form of
    # "--admin-password-env", which is exactly how a password ends up in shell history.
    parser = argparse.ArgumentParser(prog="mqs-device", allow_abbrev=False,
                                     description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    discover = sub.add_parser("discover", allow_abbrev=False,
                              help="MNDP scan of the local networks")
    discover.add_argument("--seconds", type=int, default=5)

    connect = sub.add_parser("connect", help="connect and describe the device", allow_abbrev=False)
    _add_connection_args(connect, host_required=False)

    apply_cmd = sub.add_parser("apply", allow_abbrev=False,
                               help="generate, import, or reset + import")
    _add_connection_args(apply_cmd, host_required=False)
    source = apply_cmd.add_mutually_exclusive_group(required=True)
    source.add_argument("--profile", help="name of a saved profile")
    source.add_argument("--profile-file", help="path to an exported profile JSON")
    apply_cmd.add_argument("--mode", default=ApplyMode.GENERATE_ONLY.value,
                           choices=[m.value for m in ApplyMode])
    apply_cmd.add_argument("--target", default="7.15",
                           help="RouterOS version for --mode generate without --host")
    apply_cmd.add_argument("--reset-delay", type=int, default=15,
                           help="seconds the uploaded script waits before it starts")
    apply_cmd.add_argument("--rollback-minutes", type=int,
                           help="restore the backup unless confirmed (import only)")
    apply_cmd.add_argument("--download-dir", help="where to put the pre-apply backup and export")
    apply_cmd.add_argument("--keep-backups", type=int, default=DEFAULT_KEEP_BACKUPS, metavar="N",
                           help="pre-apply backups to keep on the device, the new one "
                                f"included (default {DEFAULT_KEEP_BACKUPS}; 0 keeps all)")
    apply_cmd.add_argument("--admin-password-env", default="MQS_ADMIN_PASSWORD", metavar="VAR",
                           help="environment variable with the new admin password (reset)")
    apply_cmd.add_argument("--keep-users", action="store_true",
                           help="reset with keep-users=yes: users and their passwords stay, "
                           "the admin password is optional, and the apply reconnects as the "
                           "same user")
    apply_cmd.add_argument("--update", action="store_true",
                           help="reset only: upload the RouterOS package for the device's "
                           "architecture from the Update directory, so the reset's reboot "
                           "installs it")
    apply_cmd.add_argument("--update-dir", metavar="DIR",
                           help=f"with --update: where the packages are (default "
                           f"{update_dir()})")
    apply_cmd.add_argument("--confirm-identity", metavar="IDENTITY",
                           help="the device identity, required by reset")
    apply_cmd.add_argument("--allow-dialect-mismatch", action="store_true")
    apply_cmd.add_argument("--show-script", action="store_true",
                           help="print the script that would be uploaded")
    apply_cmd.add_argument("--out", help="write the generated script here (--mode generate)")
    apply_cmd.add_argument("-y", "--yes", action="store_true",
                           help="do not ask before changing the device")
    apply_cmd.add_argument("-v", "--verbose", action="store_true",
                           help="add the technical detail of each step")
    return parser


COMMANDS: dict[str, Callable[..., int]] = {
    "discover": cmd_discover,
    "connect": cmd_connect,
    "apply": cmd_apply,
}


def main(argv: Sequence[str] | None = None, *, out: Any = None, **deps: Any) -> int:
    args = build_parser().parse_args(argv)
    mac = getattr(args, "mac", None)
    if args.command == "connect" and not args.host and not mac:
        print("--host or --mac is required", file=sys.stderr)
        return BLOCKED
    if args.command == "apply" and args.mode != ApplyMode.GENERATE_ONLY.value \
            and not args.host and not mac:
        print(f"--host is required for --mode {args.mode} (or --mac)", file=sys.stderr)
        return BLOCKED
    if mac and not args.temp_address:
        print("--mac needs --temp-address: a free address in this PC's subnet",
              file=sys.stderr)
        return BLOCKED
    if getattr(args, "update", False) and args.mode != ApplyMode.RESET_AND_IMPORT.value:
        print("--update needs --mode reset: the packages are installed by its reboot",
              file=sys.stderr)
        return BLOCKED
    try:
        return COMMANDS[args.command](args, out or sys.stdout, **deps)
    except CliError as exc:
        print(str(exc), file=sys.stderr)
        return FAILED
    except TransportError as exc:  # refused, unreachable, gone: nothing a traceback explains
        print(f"{type(exc).__name__}: {exc}{refusal_hint(exc, args)}", file=sys.stderr)
        return FAILED


def refusal_hint(exc: TransportError, args: argparse.Namespace) -> str:
    """What to change when api-ssl refused a login that brought only a key, else ""."""
    if not isinstance(exc, AuthError) or exc.kind != TransportKind.API_SSL:
        return ""
    # By address with a key the password is never asked for: it is the variable or none.
    # By MAC it may have been typed in, so an empty variable does not mean there was none.
    if not getattr(args, "key", None) or args.mac or _password(args.password_env, "", ask=False):
        return ""
    return (f" (api-ssl logs in with a password only and {args.password_env} holds none;"
            " the key is used over SSH: --transport ssh --ssh-port <port>)")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
