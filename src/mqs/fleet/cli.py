"""`mqs fleet update` — the batch, from the command line.

Reads the inventory a scan wrote, plans every device, shows the order, and only then asks.
The dry run is the default mode of use rather than an afterthought: on a network of
antennas the plan is the thing worth checking, and it costs nothing to look at.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mqs.apply.packages import UpdateDir, package_arch, scan_update_dir
from mqs.discover.candidates import Candidate
from mqs.discover.cli import INVENTORY_NAME
from mqs.discover.store import InventoryError, load
from mqs.discover.topology import describe
from mqs.fleet.batch import (
    BatchPlan,
    BatchRunner,
    DeviceStep,
    Outcome,
    dry_run_lines,
    plan_batch,
)
from mqs.issues import Issue, Severity
from mqs.model.version import RosVersion
from mqs.paths import app_data_dir

OK = 0
FAILED = 1
BLOCKED = 2
REFUSED = 3


def add_fleet_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--to", metavar="VERSION", required=True,
                        help="the RouterOS version every device should end up on")
    parser.add_argument("--inventory", metavar="FILE",
                        help=f"the inventory to work from "
                             f"(default {app_data_dir() / INVENTORY_NAME})")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the order and what each device would get, and change "
                        "nothing. Worth doing first, every time")
    parser.add_argument("--parallel", type=int, default=1, metavar="N",
                        help="how many devices at once (default 1). A device with "
                        "anything behind it always runs alone whatever this says")
    parser.add_argument("--only", action="append", metavar="NAME",
                        help="limit the batch to these devices, by identity or address. "
                        "Repeatable")
    parser.add_argument("--here", metavar="NAME",
                        help="the device this PC is behind; it goes last whatever its "
                        "depth, because rebooting it ends the batch")
    parser.add_argument("--confirm-order", action="store_true",
                        help="accept an order the application is not sure of (an "
                        "incomplete topology). Without it such a batch refuses to run")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="do not ask before starting")


def _name(device: Candidate) -> str:
    return device.identity or device.address or device.key


def chosen(devices: list[Candidate], only: list[str] | None) -> list[Candidate]:
    """``--only`` matched against identity and address, so either spelling works."""
    if not only:
        return devices
    wanted = {text.strip().lower() for text in only}
    return [d for d in devices
            if d.identity.lower() in wanted
            or any(a.lower() in wanted for a in d.addresses)]


def key_of(devices: list[Candidate], name: str) -> str:
    match = next((d for d in devices
                  if d.identity == name or name in d.addresses), None)
    return match.key if match else ""


def print_plan(plan: BatchPlan, out: Any) -> None:
    print(f"\n{len(plan.steps)} device(s), in this order:", file=out)
    for line in dry_run_lines(plan):
        print(f"  {line}", file=out)
    tree = describe(plan.topology, [s.device for s in plan.steps])
    if tree:
        print("\ntopology:", file=out)
        for line in tree:
            print(f"  {line}", file=out)


def print_issues(issues: tuple[Issue, ...], out: Any) -> None:
    for issue in issues:
        print(f"  {issue.severity.value}: {issue.message}", file=out)


def cmd_fleet(
    args: argparse.Namespace,
    out: Any,
    *,
    update_one: Callable[[DeviceStep], None] | None = None,
    found_for: Callable[[Candidate], UpdateDir] | None = None,
    **deps: Any,
) -> int:
    """Plan the whole batch, show it, then run it — or stop at any of those."""
    try:
        target = RosVersion.parse(args.to)
    except ValueError as exc:
        print(f"--to {args.to}: {exc}", file=sys.stderr)
        return BLOCKED

    path = (Path(args.inventory) if args.inventory
            else app_data_dir() / INVENTORY_NAME)
    try:
        known = load(path).devices
    except InventoryError as exc:
        print(str(exc), file=sys.stderr)
        return FAILED
    if not known:
        print(f"{path} holds no devices; run `mqs scan --login` first", file=sys.stderr)
        return BLOCKED

    devices = chosen(known, args.only)
    if not devices:
        print("--only matched no device in the inventory", file=sys.stderr)
        return BLOCKED

    packages = found_for or _packages_for(args)
    plan = plan_batch(devices, target, packages,
                      here=key_of(devices, args.here) if args.here else "",
                      wifi_package=_wifi(args), allow_missing=_allow_missing(args))
    print_issues(plan.issues, out)
    print_plan(plan, out)

    if args.dry_run:
        print("\ndry run: nothing was changed on any device", file=out)
        return OK
    if not plan.ready:
        print_issues((Issue("fleet.blocked", Severity.ERROR, "", {
            "count": len(plan.blocked),
            "names": ", ".join(s.name for s in plan.blocked)}),), out)
        return BLOCKED
    if plan.order.needs_confirmation and not args.confirm_order:
        print("\nthe order is not certain; check it and pass --confirm-order",
              file=sys.stderr)
        return BLOCKED
    if not args.yes and not _asked(out):
        return REFUSED
    if update_one is None:
        print("no way to update a device was provided", file=sys.stderr)
        return BLOCKED

    print(f"\nupdating {len(plan.runnable)} device(s) to {target}:", file=out)
    report = BatchRunner(plan, run_one=update_one, parallel=args.parallel,
                         on_step=lambda step: _report(step, out)).run()
    if report.stopped_by is not None:
        rest = len(report.of(Outcome.SKIPPED))
        print_issues((Issue("fleet.stopped", Severity.ERROR, "", {
            "name": report.stopped_by.name, "error": report.stopped_by.detail,
            "rest": rest}),), out)
        return FAILED
    print(f"\n{len(report.of(Outcome.DONE))} device(s) done", file=out)
    return OK if report.succeeded else FAILED


def _report(step: DeviceStep, out: Any) -> None:
    if step.outcome is Outcome.RUNNING:
        print(f"  … {step.name}", file=out, flush=True)
    elif step.outcome is Outcome.DONE:
        print(f"  OK {step.name}", file=out, flush=True)
    elif step.outcome is Outcome.FAILED:
        behind = (f" ({len(step.behind)} device(s) behind it)" if step.behind else "")
        print(f"  CHYBA {step.name}{behind}: {step.detail}", file=out, flush=True)


def _asked(out: Any) -> bool:
    if not sys.stdin.isatty():
        print("not a terminal and --yes was not given; nothing was changed", file=out)
        return False
    answer = input("run this batch? [y/N] ").strip().lower()
    return answer in ("y", "yes", "a", "ano")


def _packages_for(args: argparse.Namespace) -> Callable[[Candidate], UpdateDir]:
    """One scan of the directory per architecture, reused across the fleet.

    A shelf of forty CPEs is forty devices and one architecture; reading the directory
    forty times would be forty times the work for the same answer.
    """
    from mqs.cli import packages_dir

    directory = packages_dir(args)
    cache: dict[str, UpdateDir] = {}

    def found(device: Candidate) -> UpdateDir:
        inventory = device.inventory
        arch = (package_arch(inventory) or "") if inventory else ""
        if arch not in cache:
            cache[arch] = scan_update_dir(directory)
        return cache[arch]

    return found


def _wifi(args: argparse.Namespace) -> Any:
    from mqs.cli import wifi_package

    return wifi_package(args)


def _allow_missing(args: argparse.Namespace) -> list[str]:
    return list(getattr(args, "allow_missing_package", None) or ())


def updater(args: argparse.Namespace, out: Any, *,
            changer_factory: Callable[..., Any] | None = None,
            **deps: Any) -> Callable[[DeviceStep], None]:
    """Run one device's version change, the same way `mqs version` runs one.

    Raising is how a device reports failure to the batch, so nothing here swallows an
    error: a device that did not come back must stop everything behind it.

    ``changer_factory`` is the same seam `mqs version` has, and it is here for the same
    reason: without it the batch's actual work — the part that reboots hardware — could
    only be tested by rebooting hardware.
    """
    from mqs.apply.version_change import VersionChanger, plan_version
    from mqs.cli import hang_up, open_device, replace_host
    from mqs.fleet.device import update_device

    build = changer_factory or VersionChanger
    found_for = _packages_for(args)

    def update(step: DeviceStep) -> None:
        target = step.plan.target if step.plan is not None else None

        def replan(inventory: Any) -> Any:
            return plan_version(inventory, found_for(step.device), target,
                                wifi_package=_wifi(args), allow_missing=_allow_missing(args))

        update_device(
            step,
            lambda address: open_device(replace_host(args, address), out, **deps),
            on_event=lambda event: _event(step, event, out),
            close=lambda session: hang_up(session, out),
            replan=replan if target is not None else None,
            changer_factory=build,
            download_dir=Path(args.download_dir) if getattr(args, "download_dir", None)
            else None)

    return update


def _event(step: DeviceStep, event: Any, out: Any) -> None:
    if event.status.value in ("failed", "warning"):
        print(f"     {step.name}: {event.message}", file=out, flush=True)


def now() -> float:
    return time.time()
