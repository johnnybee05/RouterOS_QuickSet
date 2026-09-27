"""RouterOS packages in the Update directory: reading them and picking one for a device."""

from __future__ import annotations

from pathlib import Path

import pytest

from factories import make_inventory, make_npk
from mqs.apply.update import (
    ARCHITECTURES,
    NpkError,
    UpdateDir,
    firmware_newer,
    leftover_packages,
    plan_update,
    read_npk,
    routerboot_note,
    scan_update_dir,
)
from mqs.issues import Severity, codes
from mqs.model.inventory import PackageInfo, RouterboardInfo
from mqs.model.version import RosVersion
from mqs.paths import update_dir

# The first bytes of MikroTik's own packages, as downloaded (2026-09-17). A header built
# by this test from what the parser expects would only prove the two agree.
REAL_ARM_HEADER = bytes.fromhex(
    "1ef1d0baae7bbb0019000000000001002400000073797374656d000000000000000000000766"
    "17074389aa6a0000000000000000030000001800090000006c6f6e672d7465726d10000300000061726d")
REAL_MIPSBE_HEADER = bytes.fromhex(
    "1ef1d0ba8c5cae0019000000000001002400000073797374656d000000000000000000000766"
    "17074389aa6a0000000000000000030000001800090000006c6f6e672d7465726d1000040000006d697073")
# routeros-mmips-6.49.21.npk (downloaded 2026-09-16), up to and including the part 1 of
# the first package inside the bundle: a v6 package names itself in part 18.
REAL_MMIPS_V6_HEADER = bytes.fromhex(
    "1ef1d0ba8080a100190000000000120020000000726f757465726f732d6d6d6970730000156631"
    "06e35d996a00000000000000001800090000006c6f6e672d7465726d1000050000006d6d697073"
    "02001b000000526f757465724f5320666f72204d4d49505320646576696365730a150000000000"
    "140022000000010069707636000000000000000000000000000000000000000000000000000000"
    "0001002400000073797374656d0000000000000000000015663106e35d996a0000000000000000"
    "030000001000050000006d6d697073")


npk = make_npk
MB = 1024 * 1024


def write(directory: Path, file: str, data: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / file
    path.write_bytes(data)
    return path


def test_the_header_of_a_real_package_is_read(tmp_path):
    package = read_npk(write(tmp_path, "routeros-7.23.7-arm.npk", REAL_ARM_HEADER))
    assert (package.name, str(package.version), package.architecture, package.channel) == (
        "system", "7.23.7", "arm", "long-term")
    assert package.display_name == "routeros"
    assert package.size == len(REAL_ARM_HEADER)
    # The mipsbe package calls its architecture "mips".
    mips = read_npk(write(tmp_path, "routeros-7.23.7-mipsbe.npk", REAL_MIPSBE_HEADER))
    assert mips.architecture == "mips"


def test_the_builder_matches_the_real_layout(tmp_path):
    built = read_npk(write(tmp_path, "a.npk", npk()))
    real = read_npk(write(tmp_path, "b.npk", REAL_ARM_HEADER))
    assert (built.name, built.version, built.architecture, built.channel) == (
        real.name, real.version, real.architecture, real.channel)


def test_the_file_name_does_not_matter(tmp_path):
    package = read_npk(write(tmp_path, "renamed.npk", npk(arch="mmips")))
    assert package.architecture == "mmips" and package.file_name == "renamed.npk"


@pytest.mark.parametrize(("data", "message"), [
    (b"PK\x03\x04" + b"\0" * 60, "not a RouterOS package"),
    (npk(kind=b"b"), "not a final release"),
    (npk()[:20], "ends inside its header"),
    (npk(arch=""), "no architecture"),
])
def test_what_is_not_a_usable_package_is_refused(tmp_path, data, message):
    with pytest.raises(NpkError, match=message):
        read_npk(write(tmp_path, "x.npk", data))


def test_a_version_without_a_patch_number(tmp_path):
    assert str(read_npk(write(tmp_path, "x.npk", npk(version=(7, 20, 0)))).version) == "7.20"


def test_scan_takes_npk_files_newest_first_and_names_the_unusable(tmp_path):
    folder = tmp_path / "Update"
    write(folder, "routeros-7.22.1-arm.npk", npk(version=(7, 22, 1)))
    write(folder, "routeros-7.23.7-arm.npk", npk())
    write(folder, "broken.NPK", b"garbage")
    write(folder, "readme.txt", b"not a package")
    (folder / "old").mkdir()
    write(folder / "old", "routeros-7.24.0-arm.npk", npk(version=(7, 24, 0)))
    found = scan_update_dir(folder)
    assert found.exists
    assert [p.file_name for p in found.packages] == ["routeros-7.23.7-arm.npk",
                                                     "routeros-7.22.1-arm.npk"]
    assert [name for name, _ in found.unreadable] == ["broken.NPK"]
    assert not scan_update_dir(tmp_path / "missing").exists


def device(version: str = "7.20.7", arch: str = "arm", packages=(), free: int = 0):
    return make_inventory(version, architecture=arch, free_hdd_space=free, packages=[
        PackageInfo(name="routeros", version=version), *packages])


def v6_device(version: str = "6.49.10", arch: str = "mmips", free_ram: int = 227 * MB):
    """A v6 hEX S as it prints itself: the bundle, then every package in it, and a
    `flash/` directory, which means the file root is a RAM disk. The 4.6 MB of free
    flash are the board's own, and are not what a package has to fit into."""
    bundle = f"routeros-{arch}"
    members = ("system", "ipv6", "wireless", "hotspot", "mpls", "routing", "ppp", "dhcp",
               "security", "advanced-tools")
    return make_inventory(version, architecture=arch, has_flash_dir=True,
                          free_hdd_space=4636 * 1024, free_memory=free_ram, packages=[
        PackageInfo(name=bundle, version=version),
        *(PackageInfo(name=m, version=version, bundle=bundle) for m in members)])


def found_in(tmp_path, *files: tuple[str, bytes]) -> UpdateDir:
    for name, data in files:
        write(tmp_path / "Update", name, data)
    (tmp_path / "Update").mkdir(exist_ok=True)
    return scan_update_dir(tmp_path / "Update")


ALL_ARCHES = [(f"routeros-7.23.7-{a}.npk", npk(arch=a))
              for a in ("arm", "arm64", "mips", "mmips", "smips", "tile", "powerpc", "i386")]


@pytest.mark.parametrize(("device_arch", "expected"), [
    ("arm", "routeros-7.23.7-arm.npk"),  # the hEX S E60iUGS, verified
    ("mipsbe", "routeros-7.23.7-mips.npk"),
    ("mmips", "routeros-7.23.7-mmips.npk"),
    ("x86_64", "routeros-7.23.7-i386.npk"),
    ("powerpc", "routeros-7.23.7-powerpc.npk"),
])
def test_the_package_follows_the_device_architecture(tmp_path, device_arch, expected):
    plan = plan_update(device(arch=device_arch), found_in(tmp_path, *ALL_ARCHES))
    assert [p.file_name for p in plan.packages] == [expected]
    assert plan.target == RosVersion.parse("7.23.7") and plan.installs
    assert codes(plan.issues) == {"update.planned"}
    assert plan.issues[0].severity == Severity.INFO
    assert expected in plan.issues[0].message and "7.20.7" in plan.issues[0].message


def test_every_known_architecture_has_a_package_in_the_real_set():
    assert set(ARCHITECTURES.values()) == {"arm", "arm64", "mips", "mmips", "smips", "tile",
                                            "powerpc", "i386"}


def test_the_newest_version_wins(tmp_path):
    plan = plan_update(device(), found_in(
        tmp_path, ("a.npk", npk(version=(7, 21, 3))), ("b.npk", npk()),
        ("c.npk", npk(version=(7, 24, 1), arch="mmips"))))
    assert [p.file_name for p in plan.packages] == ["b.npk"]


@pytest.mark.parametrize(("current", "code", "severity"), [
    ("7.23.7", "update.up_to_date", Severity.INFO),
    ("7.24.1", "update.up_to_date", Severity.INFO),  # a reboot does not downgrade
    # A v6 device offered a v7 package: a migration, not an update.
    ("6.49.10", "update.other_major", Severity.WARNING),
])
def test_nothing_to_install(tmp_path, current, code, severity):
    plan = plan_update(device(current), found_in(tmp_path, ("a.npk", npk())))
    assert not plan.installs and plan.target is None
    assert [(i.code, i.severity) for i in plan.issues] == [(code, severity)]


def test_a_package_of_another_major_version_is_not_installed(tmp_path):
    plan = plan_update(device(), found_in(tmp_path, ("a.npk", npk(version=(8, 1, 0)))))
    assert not plan.installs and codes(plan.issues) == {"update.other_major"}


@pytest.mark.parametrize(("arch", "code"), [("", "update.unknown_arch"),
                                            ("sparc", "update.unknown_arch"),
                                            ("arm64", "update.no_package")])
def test_no_package_for_the_architecture(tmp_path, arch, code):
    plan = plan_update(device(arch=arch), found_in(tmp_path, ("a.npk", npk())))
    assert not plan.installs and codes(plan.issues) == {code}


def test_no_directory(tmp_path):
    plan = plan_update(device(), scan_update_dir(tmp_path / "nowhere"))
    assert not plan.installs and codes(plan.issues) == {"update.no_dir"}
    assert str(tmp_path / "nowhere") in plan.issues[0].message


def test_an_unreadable_file_is_mentioned_but_does_not_stop_the_update(tmp_path):
    plan = plan_update(device(), found_in(tmp_path, ("a.npk", npk()), ("bad.npk", b"x")))
    assert plan.installs
    assert codes(plan.issues) == {"update.planned", "update.unreadable"}


def test_extra_packages_go_along_when_the_directory_has_them(tmp_path):
    inventory = device(packages=[PackageInfo(name="wifi-qcom", version="7.20.7"),
                                 PackageInfo(name="container", version="7.20.7")])
    plan = plan_update(inventory, found_in(
        tmp_path, ("routeros.npk", npk()), ("wifi.npk", npk("wifi-qcom")),
        ("wifi-old.npk", npk("wifi-qcom", (7, 22, 0))),
        ("container-mmips.npk", npk("container", arch="mmips"))))
    assert [p.file_name for p in plan.packages] == ["routeros.npk", "wifi.npk"]
    missing = [i for i in plan.issues if i.code == "update.extra_missing"]
    assert [i.params["name"] for i in missing] == ["container"]
    assert missing[0].severity == Severity.WARNING
    assert "wifi.npk" in plan.issues[0].message


def test_the_header_of_a_real_v6_package_is_read(tmp_path):
    package = read_npk(write(tmp_path, "routeros-mmips-6.49.21.npk", REAL_MMIPS_V6_HEADER))
    # Part 18, the bundle's own info — not the part 1 of the "system" package inside it,
    # which these bytes also carry.
    assert (package.name, str(package.version), package.architecture, package.channel) == (
        "routeros-mmips", "6.49.21", "mmips", "long-term")
    assert package.is_main and package.display_name == "routeros"


def test_the_builder_matches_the_real_v6_layout(tmp_path):
    built = read_npk(write(tmp_path, "a.npk", npk("routeros-mmips", (6, 49, 21), "mmips",
                                                  bundle=True)))
    real = read_npk(write(tmp_path, "b.npk", REAL_MMIPS_V6_HEADER))
    assert (built.name, built.version, built.architecture, built.channel) == (
        real.name, real.version, real.architecture, real.channel)


def test_a_v6_device_updates_within_v6(tmp_path):
    # The directory holds a package for both major versions, as MikroTik publishes them.
    plan = plan_update(v6_device(), found_in(
        tmp_path, ("routeros-mmips-6.49.21.npk", npk("routeros-mmips", (6, 49, 21), "mmips",
                                                     bundle=True)),
        ("routeros-7.23.7-mmips.npk", npk(arch="mmips"))))
    assert [p.file_name for p in plan.packages] == ["routeros-mmips-6.49.21.npk"]
    assert str(plan.target) == "6.49.21"
    # The bundle and the ten packages in it all come in that one file.
    assert codes(plan.issues) == {"update.planned"}


def test_a_v6_device_is_not_moved_to_v7(tmp_path):
    plan = plan_update(v6_device(), found_in(tmp_path, ("a.npk", npk(arch="mmips"))))
    assert not plan.installs and codes(plan.issues) == {"update.other_major"}
    assert plan.issues[0].params["version"] == "7.23.7"


def test_a_ram_disk_board_measures_the_package_against_its_memory(tmp_path):
    # hEX S 6.49.10, 2026-09-27: a 10.1 MB package went up with 4.6 MB of free flash,
    # free-hdd-space did not move, and free-memory fell by the same amount.
    data = npk("routeros-mmips", (6, 49, 21), "mmips", bundle=True)
    found = found_in(tmp_path, ("a.npk", data))
    assert plan_update(v6_device(free_ram=len(data)), found).installs
    plan = plan_update(v6_device(free_ram=len(data) - 1), found)
    assert not plan.installs and codes(plan.issues) == {"update.no_ram"}
    assert plan.issues[0].params["version"] == "6.49.21"
    # A device that does not say how much room it has is not stopped.
    assert plan_update(v6_device(free_ram=0), found).installs


def test_a_board_without_a_flash_directory_measures_it_against_the_flash(tmp_path):
    data = npk()
    found = found_in(tmp_path, ("a.npk", data))
    # Plenty of memory, no room in the flash the root sits on.
    cramped = make_inventory("7.20.7", architecture="arm", free_hdd_space=len(data) - 1,
                             free_memory=512 * MB,
                             packages=[PackageInfo(name="routeros", version="7.20.7")])
    plan = plan_update(cramped, found)
    assert not plan.installs and codes(plan.issues) == {"update.no_space"}
    assert plan_update(device(free=len(data)), found).installs


def test_packages_only_offered_for_download_are_not_missing(tmp_path):
    # 7.23 lists every package MikroTik publishes for the board; the ones that are not
    # installed come with the flags XA and no version, and need no file of their own.
    inventory = device(packages=[PackageInfo(name="wireless", available=True, disabled=True),
                                 PackageInfo(name="container", available=True, disabled=True),
                                 PackageInfo(name="wifi-qcom", version="7.20.7")])
    plan = plan_update(inventory, found_in(tmp_path, ("routeros.npk", npk()),
                                           ("wifi.npk", npk("wifi-qcom"))))
    assert [p.file_name for p in plan.packages] == ["routeros.npk", "wifi.npk"]
    assert codes(plan.issues) == {"update.planned"}


def test_leftover_packages_are_only_the_uploaded_ones(tmp_path):
    plan = plan_update(device(), found_in(tmp_path, ("routeros-7.23.7-arm.npk", npk())))
    assert leftover_packages(["routeros-7.23.7-arm.npk", "other.npk", "x.rsc"], plan) == [
        "routeros-7.23.7-arm.npk"]
    assert leftover_packages(["x.rsc"], plan) == []


def test_the_update_directory_is_next_to_the_exe(tmp_path):
    exe = tmp_path / "MikrotikQuickSetPlus.exe"
    assert update_dir(str(exe), frozen=True) == tmp_path.resolve() / "Update"
    # From the sources: where build.ps1 puts the exe.
    assert update_dir(frozen=False).parts[-2:] == ("dist", "Update")


def board(current: str, available: str, routerboard: bool = True):
    inventory = device()
    return inventory.model_copy(update={"routerboard": RouterboardInfo(
        routerboard=routerboard, model="E60iUGS", current_firmware=current,
        upgrade_firmware=available)})


def test_the_routerboot_note_before_the_apply(tmp_path):
    installs = plan_update(device(), found_in(tmp_path, ("a.npk", npk())))
    nothing = plan_update(device("7.23.7"), found_in(tmp_path))
    # An update brings its own RouterBOOT, whatever the board says now.
    note = routerboot_note(board("7.20.7", "7.20.7"), installs)
    assert note is not None and note.code == "update.routerboot_after"
    assert "7.23.7" in note.message and "7.20.7" in note.message
    # No update, the RouterOS already on the device brings a newer one (the hEX S now).
    note = routerboot_note(board("7.20.7", "7.23.7"), nothing)
    assert note is not None and note.code == "update.routerboot"
    assert routerboot_note(board("7.23.7", "7.23.7"), nothing) is None
    assert routerboot_note(board("7.20.7", "7.23.7", routerboard=False), installs) is None
    assert routerboot_note(device(), installs) is None  # no /system routerboard at all


@pytest.mark.parametrize(("available", "current", "newer"), [
    ("7.23.7", "7.20.7", True), ("7.20.7", "7.20.7", False), ("7.19", "7.20.7", False),
    ("", "7.20.7", False), ("7.23.7", "", False)])
def test_firmware_newer(available, current, newer):
    assert firmware_newer(available, current) is newer
