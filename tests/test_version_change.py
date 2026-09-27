"""Planning a move to a named RouterOS version: the floor, the v6/v7 gate, the route."""

from __future__ import annotations

from pathlib import Path

import pytest

from factories import make_inventory
from factories import make_npk as npk
from mqs.apply.packages import scan_update_dir
from mqs.apply.version_change import (
    V6_TO_V7_FROM,
    Install,
    available_versions,
    plan_version,
)
from mqs.issues import Severity, codes
from mqs.model.inventory import PackageInfo
from mqs.model.version import RosVersion

MB = 1024 * 1024
V = RosVersion.parse


def write(directory: Path, file: str, data: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / file).write_bytes(data)
    return directory / file


def v6_npk(version: tuple[int, int, int], arch: str = "mmips") -> bytes:
    """A v6 package: a bundle that names itself `routeros-<arch>` in part 18."""
    return npk(f"routeros-{arch}", version, arch, bundle=True)


def mmips_dir(tmp_path: Path, *versions: tuple[int, int, int]) -> Path:
    """The Update directory as it stands on this PC: v6 and v7 for the same board."""
    folder = tmp_path / "Update"
    for version in versions or ((6, 49, 12), (6, 49, 21), (7, 23, 5)):
        if version[0] == 6:
            write(folder, f"routeros-mmips-{version[0]}.{version[1]}.{version[2]}.npk",
                  v6_npk(version))
        else:
            write(folder, f"routeros-{version[0]}.{version[1]}.{version[2]}-mmips.npk",
                  npk("system", version, "mmips"))
    folder.mkdir(exist_ok=True)
    return folder


def mmips(version: str = "6.49.21", *, factory: str = "6.42.4", free_ram: int = 227 * MB,
          extras: tuple[str, ...] = ()) -> object:
    """The v6 hEX S RB760iGS as it prints itself: the bundle and its members, a `flash/`
    directory (so the file root is a RAM disk) and factory-firmware 6.42.4."""
    bundle = "routeros-mmips"
    members = ("system", "ipv6", "wireless", "hotspot", "mpls", "routing", "ppp", "dhcp",
               "security", "advanced-tools")
    return make_inventory(version, architecture="mmips", has_flash_dir=True,
                          free_hdd_space=4636 * 1024, free_memory=free_ram,
                          factory_firmware=factory, factory_software=factory, packages=[
        PackageInfo(name=bundle, version=version),
        *(PackageInfo(name=m, version=version, bundle=bundle) for m in members),
        *(PackageInfo(name=e, version=version) for e in extras)])


def arm(version: str = "7.23.5", *, factory: str = "7.20.7") -> object:
    """The arm hEX S E60iUGS: shipped with v7, so RouterOS 6 is out of its reach."""
    return make_inventory(version, architecture="arm", free_hdd_space=103 * MB,
                          factory_firmware=factory, factory_software="7.20",
                          packages=[PackageInfo(name="routeros", version=version)])


# ------------------------------------------------------------------ one hop


def test_a_downgrade_inside_one_major_version(tmp_path):
    plan = plan_version(mmips("6.49.21"), scan_update_dir(mmips_dir(tmp_path)), V("6.49.12"))
    assert plan.changes and not plan.blocked and plan.reboots == 1
    hop = plan.hops[0]
    assert hop.install == Install.BY_DOWNGRADE and not hop.crosses_major
    assert hop.main.file_name == "routeros-mmips-6.49.12.npk"
    assert plan.floor == V("6.42.4") and plan.downgrades and plan.needs_confirmation
    assert codes(plan.issues) == {"version.planned"}


def test_an_upgrade_inside_one_major_version_installs_by_reboot(tmp_path):
    plan = plan_version(mmips("6.49.12"), scan_update_dir(mmips_dir(tmp_path)), V("6.49.21"))
    assert [h.install for h in plan.hops] == [Install.BY_REBOOT]
    assert not plan.downgrades and not plan.needs_confirmation


def test_the_version_the_device_already_runs(tmp_path):
    plan = plan_version(mmips("6.49.21"), scan_update_dir(mmips_dir(tmp_path)), V("6.49.21"))
    assert not plan.changes and not plan.blocked
    assert [(i.code, i.severity) for i in plan.issues] == [("version.same", Severity.INFO)]


# ------------------------------------------------------------------ the floor


def test_nothing_older_than_the_factory_version(tmp_path):
    folder = mmips_dir(tmp_path, (6, 40, 9), (6, 49, 21))
    plan = plan_version(mmips("6.49.21"), scan_update_dir(folder), V("6.40.9"))
    assert plan.blocked and not plan.changes
    assert codes(plan.issues) == {"version.below_factory"}
    assert "6.42.4" in plan.issues[0].message


def test_a_board_shipped_with_v7_cannot_reach_v6(tmp_path):
    folder = mmips_dir(tmp_path, (6, 49, 21))
    write(folder, "routeros-6.49.21-arm.npk", v6_npk((6, 49, 21), "arm"))
    plan = plan_version(arm("7.23.5"), scan_update_dir(folder), V("6.49.21"))
    assert plan.blocked and codes(plan.issues) == {"version.below_factory"}


def test_a_board_that_does_not_say_what_it_was_shipped_with(tmp_path):
    """CHR and x86: the downgrade is offered, with the warning that it may be refused."""
    chr_like = make_inventory("7.23.5", architecture="x86_64", free_hdd_space=200 * MB,
                              packages=[PackageInfo(name="routeros", version="7.23.5")])
    folder = tmp_path / "Update"
    write(folder, "routeros-7.20.7.npk", npk("system", (7, 20, 7), "i386"))
    plan = plan_version(chr_like, scan_update_dir(folder), V("7.20.7"))
    assert plan.changes and plan.floor is None
    assert codes(plan.issues) == {"version.no_factory_firmware", "version.planned"}


def test_the_floor_does_not_stand_in_the_way_of_an_upgrade(tmp_path):
    plan = plan_version(mmips("6.49.12", factory=""), scan_update_dir(mmips_dir(tmp_path)),
                        V("6.49.21"))
    assert plan.changes and codes(plan.issues) == {"version.planned"}


# ------------------------------------------------------------------ between v6 and v7


def test_v6_below_the_gate_goes_in_two_hops(tmp_path):
    plan = plan_version(mmips("6.49.12"), scan_update_dir(mmips_dir(tmp_path)), V("7.23.5"))
    assert [str(h.version) for h in plan.hops] == ["6.49.21", "7.23.5"]
    assert [h.install for h in plan.hops] == [Install.BY_REBOOT, Install.BY_REBOOT]
    assert [h.crosses_major for h in plan.hops] == [False, True]
    assert plan.reboots == 2 and plan.crosses_major and plan.needs_confirmation
    assert "version.crosses_major" in codes(plan.issues)


def test_v6_at_the_gate_crosses_in_one_hop(tmp_path):
    plan = plan_version(mmips("6.49.21"), scan_update_dir(mmips_dir(tmp_path)), V("7.23.5"))
    assert [str(h.version) for h in plan.hops] == ["7.23.5"]
    assert plan.hops[0].crosses_major


def test_v6_past_the_gate_crosses_in_one_hop(tmp_path):
    """The gate is a minimum: 6.49.22 goes straight over, it is not sent back to 6.49.21."""
    folder = mmips_dir(tmp_path, (6, 49, 21), (7, 23, 5))
    plan = plan_version(mmips("6.49.22"), scan_update_dir(folder), V("7.23.5"))
    assert [str(h.version) for h in plan.hops] == ["7.23.5"]
    assert V("6.49.21") == V6_TO_V7_FROM


def test_without_the_gate_package_v7_is_refused(tmp_path):
    folder = mmips_dir(tmp_path, (6, 49, 12), (7, 23, 5))
    plan = plan_version(mmips("6.49.12"), scan_update_dir(folder), V("7.23.5"))
    assert plan.blocked and not plan.changes
    assert codes(plan.issues) == {"version.v7_needs_649"}
    assert "6.49.21" in plan.issues[0].message


def test_v7_back_to_v6_is_one_downgrade_with_the_configuration_lost(tmp_path):
    plan = plan_version(mmips("7.23.5", factory="6.42.4"),
                        scan_update_dir(mmips_dir(tmp_path)), V("6.49.21"))
    assert [h.install for h in plan.hops] == [Install.BY_DOWNGRADE]
    assert plan.hops[0].crosses_major and plan.needs_confirmation
    assert {"version.crosses_major", "version.v6_config_lost"} <= codes(plan.issues)


# ------------------------------------------------------------------ extra packages


def test_an_extra_package_comes_along_inside_one_major_version(tmp_path):
    folder = mmips_dir(tmp_path, (6, 49, 12), (6, 49, 21))
    write(folder, "wireless-6.49.12-mmips.npk", npk("wireless", (6, 49, 12), "mmips"))
    plan = plan_version(mmips("6.49.21", extras=("wireless",)), scan_update_dir(folder),
                        V("6.49.12"))
    assert [p.display_name for p in plan.hops[0].packages] == ["routeros", "wireless"]
    assert codes(plan.issues) == {"version.planned"}


def test_an_extra_package_without_a_file_is_a_warning(tmp_path):
    plan = plan_version(mmips("6.49.21", extras=("container",)),
                        scan_update_dir(mmips_dir(tmp_path)), V("6.49.12"))
    assert len(plan.hops[0].packages) == 1
    assert "version.extra_missing" in codes(plan.issues)


def test_across_a_major_version_extra_packages_are_dropped_not_asked_for(tmp_path):
    plan = plan_version(mmips("7.23.5", factory="6.42.4"),
                        scan_update_dir(mmips_dir(tmp_path)), V("6.49.21"))
    assert len(plan.hops[0].packages) == 1
    assert "version.extra_missing" not in codes(plan.issues)


def test_the_v7_packages_a_move_to_v6_leaves_behind_are_named(tmp_path):
    device = make_inventory("7.23.5", architecture="mmips", has_flash_dir=True,
                            free_memory=227 * MB, factory_firmware="6.42.4",
                            packages=[PackageInfo(name="routeros", version="7.23.5"),
                                      PackageInfo(name="wifi-qcom", version="7.23.5"),
                                      PackageInfo(name="container", version="", available=True,
                                                  disabled=True)])
    plan = plan_version(device, scan_update_dir(mmips_dir(tmp_path)), V("6.49.21"))
    dropped = next(i for i in plan.issues if i.code == "version.extras_dropped")
    # Only what is installed: 7.23 also lists what it could download.
    assert "wifi-qcom" in dropped.message and "container" not in dropped.message


# ------------------------------------------------------------------ refusals


def test_a_version_the_directory_does_not_hold(tmp_path):
    plan = plan_version(mmips(), scan_update_dir(mmips_dir(tmp_path)), V("6.48.6"))
    assert plan.blocked and codes(plan.issues) == {"version.no_package"}


def test_a_package_for_another_architecture_is_not_offered(tmp_path):
    folder = tmp_path / "Update"
    write(folder, "routeros-6.49.12-arm.npk", v6_npk((6, 49, 12), "arm"))
    plan = plan_version(mmips(), scan_update_dir(folder), V("6.49.12"))
    assert plan.blocked and codes(plan.issues) == {"version.no_package"}


@pytest.mark.parametrize("arch", ["", "sparc"])
def test_an_architecture_the_application_does_not_know(tmp_path, arch):
    device = make_inventory("6.49.21", architecture=arch, factory_firmware="6.42.4")
    plan = plan_version(device, scan_update_dir(mmips_dir(tmp_path)), V("6.49.12"))
    assert plan.blocked and codes(plan.issues) == {"version.unknown_arch"}
    assert available_versions(device, scan_update_dir(mmips_dir(tmp_path))) == []


def test_no_directory(tmp_path):
    plan = plan_version(mmips(), scan_update_dir(tmp_path / "nowhere"), V("6.49.12"))
    assert plan.blocked and codes(plan.issues) == {"version.no_dir"}


@pytest.mark.parametrize(("flash", "code"), [(True, "version.no_ram"),
                                             (False, "version.no_space")])
def test_a_package_that_does_not_fit_where_the_reboot_installs_it_from(tmp_path, flash, code):
    device = make_inventory("6.49.21", architecture="mmips", has_flash_dir=flash,
                            free_memory=1 * MB if flash else 0,
                            free_hdd_space=1 * MB, factory_firmware="6.42.4",
                            packages=[PackageInfo(name="routeros-mmips", version="6.49.21")])
    folder = mmips_dir(tmp_path)
    big = folder / "routeros-mmips-6.49.12.npk"
    big.write_bytes(v6_npk((6, 49, 12)) + b"\0" * (2 * MB))
    plan = plan_version(device, scan_update_dir(folder), V("6.49.12"))
    assert plan.blocked and not plan.changes and codes(plan.issues) == {code}


def test_an_unreadable_file_is_mentioned_but_does_not_stop_the_move(tmp_path):
    folder = mmips_dir(tmp_path)
    write(folder, "broken.npk", b"garbage")
    plan = plan_version(mmips("6.49.21"), scan_update_dir(folder), V("6.49.12"))
    assert plan.changes and codes(plan.issues) == {"version.unreadable", "version.planned"}


# ------------------------------------------------------------------ what the window offers


def test_the_versions_offered_for_a_v6_board(tmp_path):
    offers = available_versions(mmips("6.49.21"), scan_update_dir(mmips_dir(tmp_path)))
    assert [str(o.version) for o in offers] == ["7.23.5", "6.49.21", "6.49.12"]
    assert [o.available for o in offers] == [True, False, True]
    assert next(o for o in offers if o.current).version == V("6.49.21")
    assert all(o.blocked_by is None for o in offers)


def test_the_versions_offered_for_a_board_shipped_with_v7(tmp_path):
    folder = mmips_dir(tmp_path, (7, 20, 7), (7, 23, 5))
    write(folder, "routeros-6.49.21-arm.npk", v6_npk((6, 49, 21), "arm"))
    write(folder, "routeros-7.20.7-arm.npk", npk("system", (7, 20, 7), "arm"))
    write(folder, "routeros-7.23.5-arm.npk", npk("system", (7, 23, 5), "arm"))
    offers = available_versions(arm("7.23.5"), scan_update_dir(folder))
    assert [str(o.version) for o in offers] == ["7.23.5", "7.20.7", "6.49.21"]
    assert [o.available for o in offers] == [False, True, False]
    blocked = next(o for o in offers if o.version == V("6.49.21"))
    assert blocked.blocked_by is not None and blocked.blocked_by.code == "version.below_factory"
