"""RouterOS update from the "Update" directory, installed by the reboot of a reset.

The directory holds `.npk` packages as MikroTik publishes them, one per architecture
(routeros-7.23.7-arm.npk, routeros-7.23.7-mmips.npk, ...). What a package is comes from
its own header, not from its file name: the header names the package, its version and
the architecture it is built for, so a renamed file is still picked correctly.

Reset + aplikovat uploads the package for the device's architecture next to the
run-after-reset script; RouterOS installs a package it finds in its file root when it
reboots, and the reset reboots it. Whether both happen on the same reboot is NUTNO OVĚŘIT
(docs/DIALECT_NOTES.md); the checklist reads the version afterwards either way.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from mqs.issues import Issue, Severity
from mqs.model.inventory import DeviceInventory, PackageInfo
from mqs.model.version import RosVersion

NPK_MAGIC = 0xBAD0F11E
PART_INFO = 1  # name[16], revision, release kind, minor, major, build time, ...
PART_ARCHITECTURE = 16
PART_CHANNEL = 24
MAX_HEADER_PARTS = 32  # the parts that matter come first; the file system is far bigger
RELEASE_FINAL = ord("f")
# The main package is "system" in its header and "routeros" in /system package on v7.
MAIN_PACKAGE = "system"
DEVICE_MAIN_PACKAGES = {"routeros", "system"}

# /system resource architecture-name -> the architecture in the package header.
# The header side is read from MikroTik's own 7.23.7 packages (routeros-7.23.7-mipsbe.npk
# says "mips", -ppc says "powerpc", the x86 one without a suffix says "i386"). The device
# side is OVĚŘENO only for "arm" (hEX S E60iUGS, 7.20.7); the others are NUTNO OVĚŘIT.
ARCHITECTURES: dict[str, str] = {
    "arm": "arm",
    "arm64": "arm64",
    "mipsbe": "mips",
    "mmips": "mmips",
    "smips": "smips",
    "tile": "tile",
    "powerpc": "powerpc",
    "x86": "i386",
    "x86_64": "i386",
}


class NpkError(ValueError):
    """Not a RouterOS package, or one whose header this module does not understand."""


@dataclass(frozen=True)
class NpkPackage:
    path: Path
    name: str  # as in the header: "system" for the main package
    version: RosVersion
    architecture: str  # as in the header: "arm", "mips", "i386", ...
    size: int
    channel: str = ""  # "long-term", "stable", ...

    @property
    def file_name(self) -> str:
        return self.path.name

    @property
    def display_name(self) -> str:
        return "routeros" if self.name == MAIN_PACKAGE else self.name


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


def _read_exact(handle: BinaryIO, size: int) -> bytes:
    data = handle.read(size)
    if len(data) != size:
        raise NpkError("the file ends inside its header")
    return data


def read_npk(path: Path) -> NpkPackage:
    """Name, version and architecture from the header of a RouterOS package.

    The layout, read from MikroTik's 7.23.7 packages: the magic 0xBAD0F11E and the
    payload size (both little endian), then parts of (type u16, size u32, data). Part 1
    holds the name in 16 bytes and the version as revision, release kind ('f'), minor,
    major; part 16 the architecture; part 24 the channel.
    """
    size = path.stat().st_size
    with path.open("rb") as handle:
        magic, _payload = struct.unpack("<II", _read_exact(handle, 8))
        if magic != NPK_MAGIC:
            raise NpkError("not a RouterOS package (.npk)")
        info: bytes | None = None
        architecture = channel = ""
        for _ in range(MAX_HEADER_PARTS):
            head = handle.read(6)
            if len(head) < 6:
                break
            part, length = struct.unpack("<HI", head)
            if part in (PART_INFO, PART_ARCHITECTURE, PART_CHANNEL):
                data = _read_exact(handle, length)
                if part == PART_INFO:
                    info = data
                elif part == PART_ARCHITECTURE:
                    architecture = data.decode("ascii", "replace").strip("\0")
                else:
                    channel = data.decode("ascii", "replace").strip("\0")
            else:
                handle.seek(length, 1)
            if info is not None and architecture and channel:
                break
    if info is None or len(info) < 20:
        raise NpkError("the package header has no name and version")
    if not architecture:
        raise NpkError("the package header names no architecture")
    name = info[:16].split(b"\0", 1)[0].decode("ascii", "replace")
    revision, kind, minor, major = info[16:20]
    if kind != RELEASE_FINAL:
        # Only final releases have been seen; how a beta or rc is written is not known.
        raise NpkError(f"release kind {kind:#04x} is not a final release")
    version = RosVersion(major=major, minor=minor, patch=revision)
    return NpkPackage(path, name, version, architecture, size, channel)


@dataclass(frozen=True)
class UpdateDir:
    directory: Path
    exists: bool
    packages: tuple[NpkPackage, ...] = ()
    unreadable: tuple[tuple[str, str], ...] = ()  # (file name, why)


def scan_update_dir(directory: Path) -> UpdateDir:
    """Every `.npk` in ``directory`` (not its subdirectories), newest first."""
    if not directory.is_dir():
        return UpdateDir(directory, exists=False)
    packages: list[NpkPackage] = []
    unreadable: list[tuple[str, str]] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".npk":
            continue
        try:
            packages.append(read_npk(path))
        except (OSError, NpkError, struct.error) as exc:
            unreadable.append((path.name, str(exc)))
    packages.sort(key=lambda p: p.version, reverse=True)
    return UpdateDir(directory, True, tuple(packages), tuple(unreadable))


def _issue(code: str, severity: Severity, **params: object) -> Issue:
    return Issue(code, severity, "", dict(params))


def plan_update(inventory: DeviceInventory, found: UpdateDir) -> UpdatePlan:
    """The packages to upload so the device comes back on the newest main package in
    ``found`` built for its architecture, or no packages and the reason why not.

    Only upgrades within RouterOS 7: going between v6 and v7 is a migration of its own,
    and a package older than the running system is not installed by a reboot at all
    (that takes `/system package downgrade`).
    """
    current = inventory.version
    notes = tuple(_issue("update.unreadable", Severity.WARNING, file=name, error=why)
                  for name, why in found.unreadable)

    def skip(code: str, severity: Severity = Severity.WARNING, **params: object) -> UpdatePlan:
        return UpdatePlan(current=current,
                          issues=(*notes, _issue(code, severity, **params)))

    if not found.exists:
        return skip("update.no_dir", dir=str(found.directory))
    if current.major != 7:
        return skip("update.v7_only", current=str(current))
    device_arch = inventory.resource.architecture
    package_arch = ARCHITECTURES.get(device_arch)
    if package_arch is None:
        return skip("update.unknown_arch", arch=device_arch or "?")
    mains = [p for p in found.packages
             if p.name == MAIN_PACKAGE and p.architecture == package_arch]
    if not mains:
        return skip("update.no_package", arch=device_arch, dir=str(found.directory))
    newest = mains[0]  # scan_update_dir sorts newest first
    if newest.version.major != current.major:
        return skip("update.other_major", version=str(newest.version), current=str(current))
    if newest.version <= current:
        return skip("update.up_to_date", Severity.INFO, version=str(newest.version),
                    current=str(current))

    # Extra packages (wireless, container, ...) have to move to the same version as the
    # main one. Their header names are taken to be their /system package names: NUTNO
    # OVĚŘIT, and a mismatch only shows as "missing", it never uploads the wrong file.
    packages = [newest]
    missing: list[Issue] = []
    for installed in _extra_packages(inventory):
        match = next((p for p in found.packages if p.name == installed.name
                      and p.architecture == package_arch and p.version == newest.version), None)
        if match is None:
            missing.append(_issue("update.extra_missing", Severity.WARNING,
                                  name=installed.name, installed=installed.version or "?",
                                  version=str(newest.version), arch=device_arch))
        else:
            packages.append(match)
    planned = _issue("update.planned", Severity.INFO, version=str(newest.version),
                     current=str(current), files=", ".join(p.file_name for p in packages))
    return UpdatePlan(tuple(packages), newest.version, current, (*notes, planned, *missing))


def _extra_packages(inventory: DeviceInventory) -> list[PackageInfo]:
    return [p for p in inventory.packages if p.name not in DEVICE_MAIN_PACKAGES]


def firmware_newer(available: str, current: str) -> bool:
    """RouterBOOT ``available`` (upgrade-firmware) is newer than ``current``."""
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

    RouterOS is expected to delete a package once it has installed it (NUTNO OVĚŘIT);
    one still there would be installed by whichever reboot comes next.
    """
    uploaded = {p.file_name for p in plan.packages}
    return sorted(name for name in file_names if name in uploaded)
