"""Developer CLI: generate a script from a profile JSON, no GUI and no device needed."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from mqs.generator.base import generate
from mqs.generator.context import TargetContext
from mqs.issues import has_errors
from mqs.model.inventory import Ipv6PackageState
from mqs.model.presets import apply_preset
from mqs.model.profile import DeviceProfile, Mode
from mqs.model.validation import validate_profile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mqs-gen", description=__doc__)
    parser.add_argument("profile", nargs="?", help="profile JSON (default: Router preset)")
    parser.add_argument("--target", default="7.15", help="RouterOS version, e.g. 6.49.10")
    parser.add_argument(
        "--ports", default="ether1,ether2,ether3,ether4,ether5",
        help="comma separated ports for the default Router preset",
    )
    parser.add_argument(
        "--ipv6-package", choices=[s.value for s in Ipv6PackageState], default="unknown",
        help="assumed ipv6 package state for v6 targets",
    )
    parser.add_argument("--issues", action="store_true", help="print issues to stderr")
    parser.add_argument("-o", "--output", help="write the script to a file instead of stdout")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.profile:
        text = Path(args.profile).read_text(encoding="utf-8-sig")  # a BOM from PowerShell 5.1
        profile = DeviceProfile.model_validate_json(text)
    else:
        ports = [p for p in args.ports.split(",") if p]
        profile = apply_preset(DeviceProfile(), Mode.ROUTER, ports)

    ctx = TargetContext.offline(args.target, ipv6_package=Ipv6PackageState(args.ipv6_package))
    result = generate(profile, ctx)
    issues = [*validate_profile(profile), *result.issues]

    if args.output:
        Path(args.output).write_text(result.text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(result.text)
    if args.issues:
        for issue in issues:
            print(str(issue), file=sys.stderr)
    return 1 if has_errors(issues) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
