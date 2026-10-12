"""RouterOS packages in the "Update" directory: reading them, and picking them for a device.

The directory holds `.npk` packages as MikroTik publishes them, one directory per version
and in it one per architecture:

    Update\\6.49.23\\mmips\\routeros-mmips-6.49.23.npk
    Update\\7.24.4\\arm\\routeros-7.24.4-arm.npk, wifi-qcom-7.24.4-arm.npk, ...

What a package is comes from its own header, not from its file name nor from the directory
it sits in: the header names the package, its version and the architecture it is built
for, so a renamed or misfiled package is still picked correctly. The layout is what a
message points at when a package is missing (`package_dir`), and a flat directory of
`.npk` files still reads the same.

This is the layer both plans stand on: `apply/update.py` moves a device forward inside its
own major version, `apply/version_change.py` takes it to a version the user names, in either
direction. Everything here is pure: files and inventory in, packages and issues out.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from mqs.issues import Issue, Severity
from mqs.model.inventory import DeviceInventory, PackageInfo
from mqs.model.version import RosVersion

NPK_MAGIC = 0xBAD0F11E
PART_INFO = 1  # name[16], revision, release kind, minor, major, build time, ...
PART_BUNDLE_INFO = 18  # the same layout, for the bundle a v6 package is
PART_ARCHITECTURE = 16
PART_CHANNEL = 24
INFO_PARTS = (PART_INFO, PART_BUNDLE_INFO)
MAX_HEADER_PARTS = 32  # the parts that matter come first; the file system is far bigger
RELEASE_FINAL = ord("f")
# The main package is "system" in a v7 header, "routeros-<arch>" in a v6 one, and
# "routeros" (v7) or "routeros-<arch>" (v6, the bundle) in /system package on the device.
MAIN_PACKAGE = "system"
MAIN_PACKAGE_PREFIX = "routeros"
DEVICE_MAIN_PACKAGES = {"routeros", "system"}


def is_main_package(name: str) -> bool:
    """The package that carries RouterOS itself, under any of the names it goes by."""
    return name == MAIN_PACKAGE or name.startswith(MAIN_PACKAGE_PREFIX)

# /system resource architecture-name -> the architecture in the package header.
# The header side is read from MikroTik's own 7.23.7 packages (routeros-7.23.7-mipsbe.npk
# says "mips", -ppc says "powerpc", the x86 one without a suffix says "i386"). The device
# side is OVĚŘENO for "arm" (hEX S E60iUGS, 7.20.7) and "mmips" (hEX S RB760iGS, 6.49.10);
# the others are NUTNO OVĚŘIT.
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
    def is_main(self) -> bool:
        """RouterOS itself: `system` in v7, the `routeros-<arch>` bundle in v6.

        Not a v6 `system`: MikroTik publishes the bundle split into its parts as well
        (`system-6.49.23-mmips.npk` next to `routeros-mmips-6.49.23.npk`, both in the same
        version directory), and that part alone is RouterOS without ipv6, dhcp, ppp and
        the rest of the bundle.
        """
        if self.name == MAIN_PACKAGE and self.version.major < 7:
            return False
        return is_main_package(self.name)

    @property
    def display_name(self) -> str:
        return "routeros" if self.is_main else self.name


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

    A v6 package is a bundle, and puts that same info in part 18 under the bundle's own
    name (`routeros-mmips`), ahead of a part 1 for each package inside it (`system`,
    `ipv6`, `wireless@`, ...). The first info part of either kind is the file's own, so
    the members never stand in for it.
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
            if part in (*INFO_PARTS, PART_ARCHITECTURE, PART_CHANNEL):
                data = _read_exact(handle, length)
                # First wins: past the file's own header the same types come back, for
                # the packages inside a v6 bundle and inside the payload.
                if part in INFO_PARTS:
                    info = data if info is None else info
                elif part == PART_ARCHITECTURE:
                    architecture = architecture or data.decode("ascii", "replace").strip("\0")
                else:
                    channel = channel or data.decode("ascii", "replace").strip("\0")
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


DOWNLOAD_BASE = "https://download.mikrotik.com/routeros"
# The x86 package carries no architecture in its file name (`routeros-7.23.7.npk`), which
# is also why its header says `i386` while the device says `x86`. The extra packages of
# that architecture carry none either (`wireless-7.24.4.npk`, `calea-6.49.23.npk`).
URL_ARCH_OMITTED = frozenset({"x86", "x86_64"})
# /system resource architecture-name -> the architecture in a file name, and the name of
# the directory the Update directory keeps it in. The same as the device's everywhere but
# PowerPC, whose files say `ppc` (`routeros-7.24.4-ppc.npk`, `wireless-6.49.23-ppc.npk`) —
# except the v6 bundle, which is `routeros-powerpc-6.49.23.npk`. Read from MikroTik's own
# 6.49.23 and 7.24.4 packages; that the device says `powerpc` is NUTNO OVĚŘIT.
FILE_ARCHITECTURES: dict[str, str] = {"powerpc": "ppc", "x86_64": "x86"}


def file_arch(device_arch: str) -> str:
    """The architecture as MikroTik's file names and the Update directory write it."""
    return FILE_ARCHITECTURES.get(device_arch, device_arch)


def package_dir(directory: Path, version: RosVersion, device_arch: str) -> Path:
    """Where the Update directory keeps ``version`` for a device: `Update\\7.24.4\\arm`.

    Only ever used to tell the user where a missing package belongs; finding packages
    does not depend on it (`scan_update_dir`).
    """
    return directory / str(version) / file_arch(device_arch)


def npk_file_name(package: str, version: RosVersion, device_arch: str) -> str:
    """MikroTik's own file name for a package.

    The architecture in the name is the device's own `architecture-name` (`mipsbe`,
    `mmips`, `arm`), **not** the one in the header — a mipsbe file is called `-mipsbe.npk`
    and says `mips` inside. Every package puts the version first, except the v6 bundle:

        v7   routeros-7.23.7-arm.npk        wifi-qcom-7.23.7-arm.npk
        v6   routeros-mmips-6.49.21.npk     wireless-6.49.23-mmips.npk

    Only the name is built here. Whether the file is the right one is settled by reading
    its header after it arrives (`read_npk`), never by trusting this.
    """
    text = str(version)
    if is_main_package(package) and version.major < 7:
        # The bundle names the architecture even on x86 (`routeros-x86-6.49.23.npk`), and
        # on PowerPC in full (`routeros-powerpc-6.49.23.npk`).
        bundle_arch = "x86" if device_arch in URL_ARCH_OMITTED else device_arch
        return f"{MAIN_PACKAGE_PREFIX}-{bundle_arch}-{text}.npk"
    name = MAIN_PACKAGE_PREFIX if is_main_package(package) else package
    if device_arch in URL_ARCH_OMITTED:
        return f"{name}-{text}.npk"
    return f"{name}-{text}-{file_arch(device_arch)}.npk"


def npk_url(package: str, version: RosVersion, device_arch: str) -> str:
    return f"{DOWNLOAD_BASE}/{version}/{npk_file_name(package, version, device_arch)}"


@dataclass(frozen=True)
class UpdateDir:
    directory: Path
    exists: bool
    packages: tuple[NpkPackage, ...] = ()
    unreadable: tuple[tuple[str, str], ...] = ()  # (file name, why)

    def with_packages(self, extra: Iterable[NpkPackage]) -> UpdateDir:
        """The same directory plus packages found elsewhere — the download cache.

        What is already here wins: a file the user put in the Update directory themselves
        is the one they meant, and a cached copy of the same version does not displace it.
        """
        here = {(p.name, p.version, p.architecture) for p in self.packages}
        added = tuple(p for p in extra
                      if (p.name, p.version, p.architecture) not in here)
        if not added:
            return self
        packages = sorted((*self.packages, *added), key=lambda p: p.version, reverse=True)
        return UpdateDir(self.directory, True, tuple(packages), self.unreadable)


def scan_update_dir(directory: Path) -> UpdateDir:
    """Every `.npk` in ``directory`` and below it, newest first.

    Below it is where they are: `Update\\<version>\\<architecture>\\`. No part of that
    path is taken on trust — a package is what its header says — so a file in the wrong
    directory is still found as what it is, and the same file twice (a copy left in the
    top level) only means the first one by path is the one used.

    An unreadable file is named by its path below ``directory``: the same `wireless.npk`
    can be in eight architecture directories, and "which one" is the point.
    """
    if not directory.is_dir():
        return UpdateDir(directory, exists=False)
    packages: list[NpkPackage] = []
    unreadable: list[tuple[str, str]] = []
    for path in sorted(directory.rglob("*"), key=lambda p: p.relative_to(directory).parts):
        if path.suffix.lower() != ".npk" or not path.is_file():
            continue
        try:
            packages.append(read_npk(path))
        except (OSError, NpkError, struct.error) as exc:
            unreadable.append((str(path.relative_to(directory)), str(exc)))
    packages.sort(key=lambda p: p.version, reverse=True)  # stable: path order within one
    return UpdateDir(directory, True, tuple(packages), tuple(unreadable))


# ------------------------------------------------------------------ picking for a device


def issue(code: str, severity: Severity, **params: object) -> Issue:
    return Issue(code, severity, "", dict(params))


def mb(size: int) -> str:
    return f"{size / 1024 / 1024:.1f}"


def package_arch(inventory: DeviceInventory) -> str | None:
    """The header architecture of the packages this device takes, None when unknown."""
    return ARCHITECTURES.get(inventory.resource.architecture)


def main_packages(found: UpdateDir, architecture: str) -> list[NpkPackage]:
    """The RouterOS packages built for ``architecture``, newest first."""
    return [p for p in found.packages if p.is_main and p.architecture == architecture]


def extra_packages(inventory: DeviceInventory) -> list[PackageInfo]:
    """The packages that need a file of their own in the Update directory.

    Left out are the ones 7.23 merely offers for download (`PackageInfo.installed`) and,
    on v6, the bundle and everything in it: `/system package print` lists the bundle
    `routeros-mmips` and its members (system, ipv6, wireless, hotspot, mpls, routing,
    ppp, dhcp, security, advanced-tools, each with `bundle=routeros-mmips`), and the one
    `routeros-<arch>-<version>.npk` brings them all.
    """
    bundles = {p.bundle for p in inventory.packages if p.bundle}
    return [p for p in inventory.packages
            if p.installed and not p.bundle
            and p.name not in DEVICE_MAIN_PACKAGES and p.name not in bundles
            and not is_main_package(p.name)]


def find_packages(
    found: UpdateDir, architecture: str, version: RosVersion, names: Iterable[str]
) -> tuple[list[NpkPackage], list[str]]:
    """The files for ``names`` in exactly ``version`` and ``architecture``, and the ones
    with no file at all.

    Every part of the set has to match: `wifi-qcom-7.15.3-arm.npk` next to a `routeros`
    7.16.1 is the mismatch that leaves a device with a radio its RouterOS cannot drive, so
    a near miss counts as missing rather than as a file to upload.
    """
    matched: list[NpkPackage] = []
    missing: list[str] = []
    for name in sorted(names):
        match = next((p for p in found.packages if p.name == name
                      and p.architecture == architecture and p.version == version), None)
        if match is None:
            missing.append(name)
        else:
            matched.append(match)
    return matched, missing


def match_extras(
    inventory: DeviceInventory, found: UpdateDir, architecture: str, version: RosVersion
) -> tuple[list[NpkPackage], list[PackageInfo]]:
    """The files for the device's extra packages in ``version``, and the ones missing.

    Their header names are taken to be their `/system package` names: NUTNO OVĚŘIT, and a
    mismatch only shows up as "missing", it never uploads the wrong file.
    """
    matched: list[NpkPackage] = []
    missing: list[PackageInfo] = []
    for installed in extra_packages(inventory):
        match = next((p for p in found.packages if p.name == installed.name
                      and p.architecture == architecture and p.version == version), None)
        if match is None:
            missing.append(installed)
        else:
            matched.append(match)
    return matched, missing


def space_issue(inventory: DeviceInventory, packages: Sequence[NpkPackage], prefix: str,
                version: RosVersion, severity: Severity = Severity.WARNING) -> Issue | None:
    """Why the packages do not fit where a reboot installs them from, or None.

    They sit in the file root until the reboot installs them, so they have to fit. What
    they have to fit into is not the same everywhere: a board with a `flash/` directory
    has a RAM disk for a root and takes them into memory (`DeviceInventory.package_space`),
    which is how a 16 MiB v6 board updates at all.
    """
    needed = sum(p.size for p in packages)
    free = inventory.package_space
    if not free or needed <= free:
        return None
    code = f"{prefix}.no_ram" if inventory.has_flash_dir else f"{prefix}.no_space"
    return issue(code, severity, version=str(version), size=mb(needed), free=mb(free))


def unreadable_issues(found: UpdateDir, prefix: str) -> tuple[Issue, ...]:
    return tuple(issue(f"{prefix}.unreadable", Severity.WARNING, file=name, error=why)
                 for name, why in found.unreadable)
