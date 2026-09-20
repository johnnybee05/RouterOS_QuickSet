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
from datetime import datetime
from pathlib import Path
from typing import Any

from mqs.apply.orchestrator import ApplyAborted, ApplyEvent, ApplyOrchestrator, ApplyReport
from mqs.apply.plan import ApplyMode, ApplyOptions, build_upload_script, make_names
from mqs.apply.preflight import collect_existing, preflight
from mqs.apply.session import DeviceSession, build_apply_context, open_session, probe_summary
from mqs.generator.base import generate
from mqs.generator.context import TargetContext
from mqs.issues import Issue, Severity
from mqs.messages_cs import STEP_PREFIX
from mqs.model.profile import DeviceProfile
from mqs.model.validation import validate_profile
from mqs.paths import profiles_dir
from mqs.profiles.store import ProfileStore, import_profile
from mqs.transport.base import ConnectionParams, TransportError
from mqs.transport.factory import TransportChoice
from mqs.transport.mndp import MndpScanner
from mqs.transport.netifs import list_ipv4_interfaces

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


def connection_params(args: argparse.Namespace) -> ConnectionParams:
    return ConnectionParams(
        host=args.host,
        username=args.user,
        # A key is credential enough: do not stop an unattended run to ask for nothing.
        password=_password(args.password_env, f"Password for {args.user}@{args.host}: ",
                           ask=not args.key),
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
    print(f"ports       : {', '.join(inventory.port_names)}", file=out)
    print(f"switches    : {', '.join(s.type for s in inventory.switches) or '-'}", file=out)
    print(f"services    : {', '.join(f'{s.name}:{s.port}' for s in inventory.services)}", file=out)
    print(f"session port: {session.session_port or '?'}", file=out)
    print(f"probes      : {probe_summary(session.probes) or '-'}", file=out)


def print_issues(issues: Sequence[Issue], out: Any) -> None:
    for issue in issues:
        print(f"  {issue.severity.value:7} {issue.code}: {issue.message}", file=out)


def build_options(args: argparse.Namespace, mode: ApplyMode) -> ApplyOptions:
    return ApplyOptions(
        mode=mode,
        rollback_minutes=args.rollback_minutes if mode == ApplyMode.IMPORT else None,
        reset_delay_s=args.reset_delay,
        admin_password=_password(args.admin_password_env, "New admin password after reset: ")
        if mode == ApplyMode.RESET_AND_IMPORT else "",
        download_dir=Path(args.download_dir) if args.download_dir else None,
        confirm_dialect_mismatch=args.allow_dialect_mismatch,
        confirmed_identity=args.confirm_identity or "",
    )


def confirmed(args: argparse.Namespace, options: ApplyOptions, session: DeviceSession) -> bool:
    if args.yes:
        return True
    if not sys.stdin.isatty():
        print("refusing to change the device: pass --yes", file=sys.stderr)
        return False
    answer = input(f"{options.mode.value} on {session.inventory.identity}"
                   f" ({session.params.host}) — type yes: ")
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
        print(f"{device.mac}  {device.ipv4 or '-':15}  {device.identity:20}  "
              f"{device.version:10}  {device.board}  via {device.local_interface}", file=out)
    for error in scanner.errors:
        print(f"  ! {error}", file=sys.stderr)
    return OK if devices else FAILED


def cmd_connect(args: argparse.Namespace, out: Any, *,
                connect: Callable[..., DeviceSession] = open_session, **_: Any) -> int:
    session = connect(connection_params(args), TransportChoice(args.transport),
                      api_port=args.api_port, ssh_port=args.ssh_port,
                      on_step=lambda step: print(f"  … {step}", file=out, flush=True))
    describe_session(session, out)
    session.transport.close()
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
    connect: Callable[..., DeviceSession] = open_session,
    collect_existing_fn: Callable[..., Any] = collect_existing,
    orchestrator_factory: Callable[..., Any] = ApplyOrchestrator,
    now: Callable[[], datetime] = datetime.now,
    **_: Any,
) -> int:
    mode = ApplyMode(args.mode)
    if mode == ApplyMode.GENERATE_ONLY and not args.host:
        return _generate_offline(args, out)

    profile = load_profile(args)
    options = build_options(args, mode)
    session = connect(connection_params(args), TransportChoice(args.transport),
                      api_port=args.api_port, ssh_port=args.ssh_port,
                      on_step=lambda step: print(f"  … {step}", file=out, flush=True))
    describe_session(session, out)

    result = generate(profile, TargetContext.from_inventory(session.inventory, session.probes))
    existing = collect_existing_fn(session.transport) if mode != ApplyMode.GENERATE_ONLY else None
    issues = preflight(profile, result, validate_profile(profile, session.inventory), options,
                       session.inventory, existing=existing, session_port=session.session_port)
    print("\npreflight:", file=out)
    print_issues(issues, out)
    if any(i.severity == Severity.ERROR for i in issues):
        return BLOCKED

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
        print(f"aborted: {exc}", file=sys.stderr)
        return FAILED
    except TransportError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return FAILED

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
    _add_connection_args(connect, host_required=True)

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
    apply_cmd.add_argument("--admin-password-env", default="MQS_ADMIN_PASSWORD", metavar="VAR",
                           help="environment variable with the new admin password (reset)")
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
    if args.command == "apply" and args.mode != ApplyMode.GENERATE_ONLY.value and not args.host:
        print(f"--host is required for --mode {args.mode}", file=sys.stderr)
        return BLOCKED
    return COMMANDS[args.command](args, out or sys.stdout, **deps)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
