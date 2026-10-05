"""RouterOS update from the "Update" directory, installed by the reboot of a reset.

Reset + aplikovat uploads the package for the device's architecture next to the
run-after-reset script; RouterOS installs a package it finds in its file root when it
reboots, and the reset reboots it. Both happen on the same reboot — OVĚŘENO on the arm
hEX S (7.20.7 → 7.23.7) and on the mmips one (6.49.12 → 6.49.21), see
docs/DIALECT_NOTES.md.

This is the forward move inside one major version, and the only one a reboot can make by
itself. Naming a version, going back to an older one, or crossing between v6 and v7 is
`apply/version_change.py`; the packages both work from are `apply/packages.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from mqs.apply.package_set import (
    driver_packages,
    installed_drivers,
    plain_extras,
)
from mqs.apply.packages import (
    NpkPackage,
    UpdateDir,
    find_packages,
    main_packages,
    package_arch,
    scan_update_dir,
    space_issue,
    unreadable_issues,
)
from mqs.apply.packages import (
    issue as _issue,
)
from mqs.issues import Issue, Severity
from mqs.model.inventory import DeviceInventory
from mqs.model.version import RosVersion


@dataclass(frozen=True)
class UpdatePlan:
    """What an update will upload; nothing to upload means no update, and ``issues``
    say why."""

    packages: tuple[NpkPackage, ...] = ()
    target: RosVersion | None = None
    current: RosVersion | None = None
    issues: tuple[Issue, ...] = field(default_factory=tuple)

    @property
    def installs(self) -> bool:
        return bool(self.packages)

    @property
    def file_names(self) -> str:
        return ", ".join(p.file_name for p in self.packages)


def plan_update(inventory: DeviceInventory, found: UpdateDir) -> UpdatePlan:
    """The packages to upload so the device comes back on the newest main package in
    ``found`` built for its architecture, or no packages and the reason why not.

    Only upgrades within the major version the device runs: going between v6 and v7 is a
    migration of its own, and a package older than the running system is not installed by
    a reboot at all (that takes `/system package downgrade`). Both are
    `apply/version_change.py`, where the user names the version.
    """
    current = inventory.version
    notes = unreadable_issues(found, "update")

    def skip(code: str, severity: Severity = Severity.WARNING, **params: object) -> UpdatePlan:
        return UpdatePlan(current=current,
                          issues=(*notes, _issue(code, severity, **params)))

    if not found.exists:
        return skip("update.no_dir", dir=str(found.directory))
    device_arch = inventory.resource.architecture
    arch = package_arch(inventory)
    if arch is None:
        return skip("update.unknown_arch", arch=device_arch or "?")
    mains = main_packages(found, arch)
    if not mains:
        return skip("update.no_package", arch=device_arch, dir=str(found.directory))
    # Within the device's own major version: the directory may well hold both, as
    # MikroTik publishes a v6 and a v7 package for the same board.
    same_major = [p for p in mains if p.version.major == current.major]
    if not same_major:
        return skip("update.other_major", version=str(mains[0].version), current=str(current))
    newest = same_major[0]  # scan_update_dir sorts newest first
    if newest.version <= current:
        return skip("update.up_to_date", Severity.INFO, version=str(newest.version),
                    current=str(current))

    # Nothing is planned for a device whose radios nobody looked for: an empty snapshot is
    # not a router, it is an unanswered question, and the answer decides whether a driver
    # package belongs in this upload (`apply/package_set.py`).
    if not inventory.radios.collected:
        return skip("update.no_snapshot", Severity.ERROR)

    # The wireless drivers first, and on their own terms: a missing one is a refusal, not
    # a warning. A reset that comes back without the radio has lost the link it was reached
    # over, and this flow ends in a reset.
    state = installed_drivers(inventory)
    drivers = driver_packages(state, newest.version, inventory)
    if drivers.blocked:
        return UpdatePlan(current=current, issues=(*notes, *drivers.issues))
    driver_files, driver_absent = find_packages(found, arch, newest.version,
                                                drivers.required)
    if driver_absent:
        return UpdatePlan(current=current, issues=(
            *notes, *(_issue("version.driver_missing", Severity.ERROR, name=name,
                             version=str(newest.version), arch=device_arch or "?")
                      for name in driver_absent)))

    # The rest (container, gps, ...) have to move to the same version too, each in a file
    # of its own; one without a file stays a warning here. This flow picks its own target
    # rather than being given one, so there is nothing for the user to waive in advance —
    # the preflight shows the warning before the reset is armed.
    extras, absent = find_packages(found, arch, newest.version,
                                   [p.name for p in plain_extras(inventory)])
    installed_version = {p.name: p.version or "?" for p in plain_extras(inventory)}
    packages = [newest, *driver_files, *extras]
    missing = [_issue("update.extra_missing", Severity.WARNING, name=name,
                      installed=installed_version.get(name, "?"),
                      version=str(newest.version), arch=device_arch) for name in absent]

    no_room = space_issue(inventory, packages, "update", newest.version)
    if no_room is not None:
        return UpdatePlan(current=current, issues=(*notes, no_room))

    planned = _issue("update.planned", Severity.INFO, version=str(newest.version),
                     current=str(current), files=", ".join(p.file_name for p in packages))
    return UpdatePlan(tuple(packages), newest.version, current, (*notes, planned, *missing))


def firmware_newer(available: str, current: str) -> bool:
    """RouterBOOT ``available`` (upgrade-firmware) is newer than ``current``.

    After a RouterOS downgrade it is the other way round — 6.49.12 in `upgrade-firmware`
    next to 6.49.21 in `current-firmware` (2026-09-27) — and then nothing is upgraded,
    which is what keeps the RouterBOOT step out of a downgrade's way.
    """
    try:
        return RosVersion.parse(available) > RosVersion.parse(current)
    except ValueError:
        return False


def routerboot_note(inventory: DeviceInventory, plan: UpdatePlan | None) -> Issue | None:
    """What the RouterBOOT upgrade after the apply will do, for the checks before it.

    The firmware comes with RouterOS: after an update `upgrade-firmware` is the new
    version (7.23.7 on the hEX S while `current-firmware` stayed 7.20.7, 2026-09-24).
    """
    board = inventory.routerboard
    if board is None or not board.routerboard:
        return None
    if plan is not None and plan.installs:
        return _issue("update.routerboot_after", Severity.INFO,
                      current=board.current_firmware or "?", version=str(plan.target))
    if firmware_newer(board.upgrade_firmware, board.current_firmware):
        return _issue("update.routerboot", Severity.INFO, current=board.current_firmware,
                      version=board.upgrade_firmware)
    return None


def plan_from_dir(directory: Path, inventory: DeviceInventory) -> UpdatePlan:
    return plan_update(inventory, scan_update_dir(directory))


def leftover_packages(file_names: Sequence[str], plan: UpdatePlan) -> list[str]:
    """Uploaded packages still among the device's files after the reboot.

    RouterOS deletes a package once it has installed it (OVĚŘENO on both boards); one
    still there would be installed by whichever reboot comes next.
    """
    uploaded = {p.file_name for p in plan.packages}
    return sorted(name for name in file_names if name in uploaded)
