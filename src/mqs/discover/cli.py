"""`mqs scan`, `mqs inventory` and `mqs fleet` — detection from the command line.

Kept out of `mqs/cli.py`, which is already long enough, and arranged so the three
commands share one idea: a scan writes the inventory, and everything else reads it. That
is what lets a survey and an update be separate acts with a file in between, rather than
one long command somebody has to get right first time.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from mqs.discover.candidates import Candidate, Confidence, Source
from mqs.discover.classify import classify
from mqs.discover.merge import merge_candidates
from mqs.discover.portscan import ScanLimits
from mqs.discover.scan import ScanPlan, confirm, run
from mqs.discover.store import (
    ChangeKind,
    Diff,
    Inventory,
    InventoryError,
    compare,
    load,
    save,
    to_csv,
    to_rows,
)
from mqs.paths import app_data_dir
from mqs.transport.mndp import MndpScanner
from mqs.transport.netifs import list_ipv4_interfaces

OK = 0
FAILED = 1
BLOCKED = 2

INVENTORY_NAME = "inventory.json"


def inventory_path(args: argparse.Namespace) -> Path:
    return (Path(args.inventory) if getattr(args, "inventory", None)
            else app_data_dir() / INVENTORY_NAME)


# ------------------------------------------------------------------ printing


def print_devices(devices: Sequence[Candidate], out: Any) -> None:
    """One line per device, with what it is and how sure we are."""
    if not devices:
        print("  nothing found", file=out)
        return
    for device in sorted(devices, key=lambda d: (d.confidence.rank * -1, d.identity)):
        verdict = classify(device)
        mark = {Confidence.CONFIRMED: "++", Confidence.PROBABLE: "+ ",
                Confidence.UNKNOWN: "? "}[device.confidence]
        name = device.identity or "-"
        # The version, not the sentence it arrived in: an announcement carries the build
        # time after it and would push every later column off the line.
        version = device.ros_version
        print(f"  {mark} {name[:24]:24} {device.address or '-':15} "
              f"{str(version) if version else '-':10} {verdict.summary:24} "
              f"{', '.join(sorted(s.value for s in device.sources))}", file=out)
        if device.login_error:
            print(f"       login refused: {device.login_error}", file=out)
        for conflict in device.conflicts:
            print(f"       conflict: {conflict}", file=out)


def print_diff(diff: Diff, out: Any) -> None:
    if not diff.changes:
        print("\nnothing changed since the last scan", file=out)
        return
    print("\nchanges since the last scan:", file=out)
    for change in diff.changes:
        mark = "!!" if change.serious else "  "
        detail = f"  {change.detail}" if change.detail else ""
        print(f"  {mark} {change.kind.value:12} {change.name:20}{detail}", file=out)


def print_sources(result: Any, out: Any) -> None:
    for source in result.results:
        state = "ok" if source.ok else f"FAILED: {source.error}"
        print(f"  {source.source.value:10} {len(source.sightings):4} found  "
              f"{source.seconds:5.1f} s  {state}", file=out)


# ------------------------------------------------------------------ scan


def build_plan(args: argparse.Namespace) -> ScanPlan:
    return ScanPlan(
        mndp=not args.no_mndp,
        mndp_seconds=float(args.seconds),
        subnets=tuple(args.subnet or ()),
        scan_ports=bool(args.scan_ports),
        scan_limits=ScanLimits(rate=float(args.scan_rate)),
        snmp_community=args.snmp_community or "",
        source_timeout_s=float(args.source_timeout),
    )


def cmd_scan(args: argparse.Namespace, out: Any, *,
             open_device: Callable[[Candidate], Any] | None = None,
             mndp_factory: Callable[[], Any] | None = None, **_: Any) -> int:
    """Find what is out there, optionally log in, and write it down.

    Logging in is `--login` and off by default: it needs credentials, it is the only part
    of this that touches a device rather than listening, and a survey is worth running
    without it.
    """
    plan = build_plan(args)
    if not plan.anything:
        print("no source enabled: nothing to do", file=sys.stderr)
        return BLOCKED
    if (args.scan_ports or plan.snmp) and not plan.subnets:
        print("--scan-ports and --snmp-community need at least one --subnet",
              file=sys.stderr)
        return BLOCKED

    factory = mndp_factory or (lambda: MndpScanner(list_ipv4_interfaces()))
    print("scanning:", file=out)
    result = run(plan, mndp_factory=factory,
                 on_source=lambda source, state: None)
    print_sources(result, out)

    devices = list(result.devices)
    if args.login and open_device is not None:
        print("\nlogging in:", file=out)
        devices = confirm(
            devices, open_device,
            on_device=lambda device, state: print(
                f"  {device.identity or device.address or device.key}: {state}", file=out))

    path = inventory_path(args)
    try:
        known = load(path)
    except InventoryError as exc:
        print(str(exc), file=sys.stderr)
        return FAILED
    diff = compare(known.devices, devices)

    print(f"\nfound {len(devices)}:", file=out)
    print_devices(devices, out)
    print_diff(diff, out)

    if not args.no_save:
        save(path, Inventory(saved_at=time.time(), devices=list(diff.merged)))
        print(f"\ninventory: {path}", file=out)
    # A radio that disappeared or a version that went backwards is worth a non-zero exit:
    # this gets run from a scheduled task, and something has to notice.
    return FAILED if diff.serious else OK


# ------------------------------------------------------------------ inventory


def cmd_inventory(args: argparse.Namespace, out: Any, **_: Any) -> int:
    """Show or export what the last scan wrote. Reads only; changes nothing anywhere."""
    path = inventory_path(args)
    try:
        inventory = load(path)
    except InventoryError as exc:
        print(str(exc), file=sys.stderr)
        return FAILED
    devices = inventory.devices
    if args.wireless_only:
        devices = [d for d in devices if d.wireless]
    if args.confirmed_only:
        devices = [d for d in devices if d.confidence is Confidence.CONFIRMED]

    if args.export:
        text = (to_csv(to_rows(devices)) if args.format == "csv"
                else Inventory(saved_at=inventory.saved_at,
                               devices=list(devices)).model_dump_json(indent=2))
        Path(args.export).write_text(text, encoding="utf-8")
        print(f"{len(devices)} device(s) -> {args.export}", file=out)
        return OK

    print(f"{path} ({len(devices)} device(s)):", file=out)
    print_devices(devices, out)
    return OK


# ------------------------------------------------------------------ arguments


def add_scan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--seconds", type=float, default=8.0,
                        help="how long to listen for MNDP announcements")
    parser.add_argument("--no-mndp", action="store_true",
                        help="do not listen for MNDP at all")
    parser.add_argument("--subnet", action="append", metavar="CIDR",
                        help="a subnet to look in, for --scan-ports and SNMP. Repeatable")
    parser.add_argument("--scan-ports", action="store_true",
                        help="knock on 8729/122/22/8291 in the named subnets. Off by "
                        "default: it touches machines nobody asked about and proves nothing")
    parser.add_argument("--scan-rate", type=float, default=50.0, metavar="N",
                        help="addresses per second while scanning (0 = no limit)")
    parser.add_argument("--snmp-community", metavar="NAME",
                        help="read sysObjectID over SNMPv2c in the named subnets")
    parser.add_argument("--source-timeout", type=float, default=180.0, metavar="S",
                        help="how long any one source may take before it is abandoned")
    parser.add_argument("--login", action="store_true",
                        help="log in to each device found and read it. Only this makes a "
                        "device updatable; it needs --user and a password")
    parser.add_argument("--inventory", metavar="FILE",
                        help=f"the inventory file (default {app_data_dir() / INVENTORY_NAME})")
    parser.add_argument("--no-save", action="store_true",
                        help="print the result and leave the inventory file alone")


def add_inventory_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--inventory", metavar="FILE",
                        help=f"the inventory file (default {app_data_dir() / INVENTORY_NAME})")
    parser.add_argument("--export", metavar="FILE", help="write the table here")
    parser.add_argument("--format", choices=("csv", "json"), default="csv")
    parser.add_argument("--wireless-only", action="store_true",
                        help="only devices with radios")
    parser.add_argument("--confirmed-only", action="store_true",
                        help="only devices read off the device itself")


def known_devices(path: Path) -> list[Candidate]:
    """The inventory as a list, merged — what a batch is planned from."""
    return merge_candidates(load(path).devices)


def seen_sources(devices: Sequence[Candidate]) -> set[Source]:
    return {s for d in devices for s in d.sources}


def serious_changes(diff: Diff) -> list[str]:
    return [f"{c.kind.value} {c.name} {c.detail}".strip()
            for c in diff.changes if c.serious]


def kind_counts(diff: Diff) -> dict[ChangeKind, int]:
    counts = dict.fromkeys(ChangeKind, 0)
    for change in diff.changes:
        counts[change.kind] += 1
    return counts
