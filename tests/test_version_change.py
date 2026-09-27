"""Planning a move to a named RouterOS version: the floor, the v6/v7 gate, the route."""

from __future__ import annotations

from pathlib import Path

import pytest

from factories import make_inventory
from factories import make_npk as npk
from fake_router import FakeRouter, RouterUploader
from mqs.apply.events import StepStatus
from mqs.apply.packages import scan_update_dir
from mqs.apply.routerboot import RouterbootState
from mqs.apply.version_change import (
    V6_TO_V7_FROM,
    Install,
    VersionChangeAborted,
    VersionChanger,
    VersionContext,
    available_versions,
    plan_version,
)
from mqs.issues import Severity, codes
from mqs.model.inventory import PackageInfo
from mqs.model.version import RosVersion
from mqs.transport.factory import ConnectOutcome
from mqs.transport.mac_connect import MacConnectMethod, MacConnectResult
from mqs.transport.upload import UploadResult, UploadStrategy

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
    # A downgrade leaves the bootloader ahead of RouterOS, and the plan says so.
    assert codes(plan.issues) == {"version.planned", "version.routerboot_stays"}


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
    assert {"version.crosses_major", "version.v7_config_converted"} <= codes(plan.issues)
    # The way up converts the configuration; only the way down loses it.
    assert "version.v6_config_lost" not in codes(plan.issues)


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
    assert "version.v7_config_converted" not in codes(plan.issues)


# ------------------------------------------------------------------ extra packages


def test_an_extra_package_comes_along_inside_one_major_version(tmp_path):
    folder = mmips_dir(tmp_path, (6, 49, 12), (6, 49, 21))
    write(folder, "wireless-6.49.12-mmips.npk", npk("wireless", (6, 49, 12), "mmips"))
    plan = plan_version(mmips("6.49.21", extras=("wireless",)), scan_update_dir(folder),
                        V("6.49.12"))
    assert [p.display_name for p in plan.hops[0].packages] == ["routeros", "wireless"]
    assert codes(plan.issues) == {"version.planned", "version.routerboot_stays"}


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
    assert plan.changes and codes(plan.issues) == {"version.unreadable", "version.planned",
                                                   "version.routerboot_stays"}


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


# ------------------------------------------------------------------ walking the route


NOW_HOSTS = ("192.168.55.129",)


class SilentUploader(RouterUploader):
    """Reports success and writes nothing: a transfer that lied about its outcome."""

    def upload(self, data, remote_path):
        self.uploaded.append(remote_path)
        return UploadResult(UploadStrategy.SFTP, remote_path)


def changer(tmp_path, target: str, current: str = "6.49.21", *, uploader=None,
            installs: bool = True, reconnect=None, versions=(), routerboot=False,
            routerboot_down=False, firmware: str = "6.42.4"):
    router = FakeRouter()
    router.version = f"{current} (long-term)"
    router.installs_packages = installs
    # As the real board reports it: the firmware it was made with, until a package puts a
    # newer image in `upgrade-firmware`.
    router.firmware.update({"current-firmware": firmware, "upgrade-firmware": firmware})
    inventory = mmips(current)
    found = scan_update_dir(mmips_dir(tmp_path, *versions))
    plan = plan_version(inventory, found, V(target))
    router.installable = {p.file_name: str(p.version)
                          for hop in plan.hops for p in hop.packages if p.is_main}
    events: list = []
    targets: list = []

    def default_reconnect(request):
        targets.append(request)
        return router.session(port=request.api_port)

    ctx = VersionContext(
        plan=plan, inventory=inventory, transport=router.session(),
        uploader_factory=lambda transport: uploader or RouterUploader(router),
        reconnect=reconnect(router, targets) if reconnect else default_reconnect,
        on_event=events.append, sleep=lambda seconds: None, alternatives=NOW_HOSTS,
        upgrade_routerboot=routerboot, routerboot_downgrade=routerboot_down,
    )
    return router, VersionChanger(ctx), events, targets


def test_a_downgrade_is_installed_and_checked(tmp_path):
    router, runner, _events, targets = changer(tmp_path, "6.49.12")
    report = runner.run()
    assert report.succeeded and [str(v) for v in report.installed] == ["6.49.12"]
    assert router.version.startswith("6.49.12") and router.reboots == 1
    # The older package takes the downgrade command, not a reboot, and the device deletes
    # it once it has installed it.
    assert "/system/package/downgrade" in router.log
    assert "/system/reboot" not in router.log
    assert not [name for name in router.files if name.endswith(".npk")]
    assert [c.ok for c in report.checks] == [True]
    assert len(targets) == 1 and targets[0].alternatives == NOW_HOSTS


def test_an_upgrade_is_installed_by_a_plain_reboot(tmp_path):
    router, runner, events, _ = changer(tmp_path, "6.49.21", current="6.49.12")
    assert runner.run().succeeded
    assert "/system/reboot" in router.log and "/system/package/downgrade" not in router.log
    assert router.version.startswith("6.49.21")
    assert {e.step for e in events if e.step.endswith(("_reboot", "_downgrade"))} == {
        "apply.step.version_install_reboot"}


def test_two_hops_are_two_reboots(tmp_path):
    router, runner, events, targets = changer(tmp_path, "7.23.5", current="6.49.12")
    report = runner.run()
    assert [str(v) for v in report.installed] == ["6.49.21", "7.23.5"]
    assert router.reboots == 2 and len(targets) == 2
    # The second package is uploaded after the first hop has rebooted, not before it.
    uploads = [e.params["file"] for e in events
               if e.step == "apply.step.version_upload" and e.status == StepStatus.OK]
    assert uploads == ["routeros-mmips-6.49.21.npk", "routeros-7.23.5-mmips.npk"]
    assert len(report.checks) == 2


def test_a_package_the_device_ignores_stops_the_route(tmp_path):
    router, runner, _events, _ = changer(tmp_path, "6.49.12", installs=False)
    with pytest.raises(VersionChangeAborted) as raised:
        runner.run()
    assert raised.value.issue.code == "apply.failed.version_not_installed"
    assert "6.49.21" in raised.value.issue.message  # what came back instead
    # Left where it was, the package would install on whatever reboot comes next.
    assert not [name for name in router.files if name.endswith(".npk")]
    assert codes(raised.value.report.issues) == {"apply.warning.version_leftover",
                                                "apply.failed.version_not_installed"}


def test_nothing_is_installed_before_the_package_is_seen_on_the_device(tmp_path):
    router, runner, _events, _ = changer(tmp_path, "6.49.12",
                                         uploader=SilentUploader(FakeRouter()))
    with pytest.raises(VersionChangeAborted) as raised:
        runner.run()
    assert raised.value.issue.code == "apply.failed.version_missing_file"
    # The downgrade command reboots even with nothing to install, so it is never issued.
    assert router.reboots == 0 and "/system/package/downgrade" not in router.log


def test_an_upload_that_fails_changes_nothing(tmp_path):
    router, runner, _events, _ = changer(tmp_path, "6.49.12",
                                         uploader=RouterUploader(FakeRouter(), fail=True))
    with pytest.raises(VersionChangeAborted) as raised:
        runner.run()
    assert raised.value.issue.code == "apply.failed.version_upload"
    assert router.reboots == 0 and router.version.startswith("6.49.21")


def test_a_device_found_again_by_mac(tmp_path):
    def by_mac(router, targets):
        def reconnect(request):
            targets.append(request)
            return MacConnectResult(ConnectOutcome(router.session()),
                                    MacConnectMethod.MAC_TELNET,
                                    temp_address="192.168.55.254/24", host="192.168.55.254")
        return reconnect

    _router, runner, _events, _targets = changer(tmp_path, "6.49.12", reconnect=by_mac)
    report = runner.run()
    assert report.succeeded and report.mac_method == "mac-telnet"
    assert report.temp_address == "192.168.55.254/24"


def test_a_route_with_nothing_to_do_touches_nothing(tmp_path):
    router, runner, events, _ = changer(tmp_path, "6.49.21")
    report = runner.run()
    assert report.succeeded and not report.installed and not events
    assert router.reboots == 0 and not router.log


# ------------------------------------------------------------------ RouterBOOT


def test_going_up_says_a_newer_routerboot_is_coming(tmp_path):
    plan = plan_version(mmips("6.49.21"), scan_update_dir(mmips_dir(tmp_path)), V("7.23.5"))
    note = next(i for i in plan.issues if i.code == "version.routerboot_after")
    assert note.severity == Severity.INFO
    assert "7.23.5" in note.message and "6.49.21" in note.message  # coming, and running


@pytest.mark.parametrize("target", ["6.49.12", "6.49.21"])
def test_going_down_or_nowhere_says_nothing_about_routerboot(tmp_path, target):
    plan = plan_version(mmips("6.49.21"), scan_update_dir(mmips_dir(tmp_path)), V(target))
    assert "version.routerboot_after" not in codes(plan.issues)


def test_a_device_without_routerboot_says_nothing_about_it(tmp_path):
    """CHR and x86: `/system routerboard` has nothing to say, so neither has the plan."""
    inventory = make_inventory("6.49.21", architecture="mmips", has_flash_dir=True,
                               free_memory=227 * MB,
                               packages=[PackageInfo(name="routeros-mmips",
                                                     version="6.49.21")])
    plan = plan_version(inventory, scan_update_dir(mmips_dir(tmp_path)), V("7.23.5"))
    assert "version.routerboot_after" not in codes(plan.issues)
    assert plan.floor is None and plan.changes  # no floor to speak of, and it still goes


def test_routerboot_is_written_once_after_the_last_hop(tmp_path):
    router, runner, events, targets = changer(tmp_path, "7.23.5", current="6.49.12",
                                              routerboot=True)
    report = runner.run()
    assert report.succeeded, [c.message for c in report.checks if not c.ok]
    assert router.log.count("/system/routerboard/upgrade") == 1  # not once per hop
    assert router.reboots == 3 and len(targets) == 3  # two hops, then the firmware
    steps = [e.step for e in events]
    assert steps.index("apply.step.routerboot") > steps.index("apply.step.version_installed")
    assert report.checks[-1].code == "verify.routerboot" and report.checks[-1].ok
    assert report.checks[-1].message == "RouterBOOT 7.23.5 (na zařízení 7.23.5)"


def test_a_downgrade_leaves_routerboot_alone_even_when_asked(tmp_path):
    """The firmware that comes with the older RouterOS is older, so there is nothing to
    write — and the step says so instead of rebooting the device for nothing."""
    router, runner, events, _ = changer(tmp_path, "6.49.12", firmware="6.49.21",
                                        routerboot=True)
    assert runner.run().succeeded
    assert "/system/routerboard/upgrade" not in router.log and router.reboots == 1
    skipped = next(e for e in events if e.step == "apply.step.routerboot_current")
    assert skipped.status == StepStatus.SKIPPED
    assert "verify.routerboot" not in {c.code for c in runner.report.checks}


def test_without_the_option_the_firmware_is_not_even_read(tmp_path):
    router, runner, _events, _ = changer(tmp_path, "7.23.5")
    assert runner.run().succeeded
    assert "/system/routerboard/print" not in router.log
    assert router.reboots == 1


def test_a_downgrade_can_take_the_bootloader_with_it_when_asked(tmp_path):
    """"Snížit RouterBOOT": the firmware an older RouterOS carries is written too. The
    device does allow it (OVĚŘENO 2026-09-27), it is simply never implied."""
    router, runner, events, _ = changer(tmp_path, "6.49.12", firmware="6.49.21",
                                        routerboot=True, routerboot_down=True)
    report = runner.run()
    assert report.succeeded, [c.message for c in report.checks if not c.ok]
    assert "/system/routerboard/upgrade" in router.log
    assert router.reboots == 2  # the version, then the firmware
    step = next(e for e in events if e.step == "apply.step.routerboot_downgrade"
                and e.status == StepStatus.OK)
    assert step.params == {"current": "6.49.21", "version": "6.49.12"}
    assert report.checks[-1].code == "verify.routerboot" and report.checks[-1].ok
    assert report.checks[-1].message == "RouterBOOT 6.49.12 (na zařízení 6.49.12)"


def test_the_switch_alone_does_not_lower_anything(tmp_path):
    """With the firmware already behind, nothing is written and no step claims otherwise."""
    router, runner, events, _ = changer(tmp_path, "6.49.21", current="6.49.12",
                                        firmware="6.49.21", routerboot=True,
                                        routerboot_down=True)
    assert runner.run().succeeded
    assert "/system/routerboard/upgrade" not in router.log and router.reboots == 1
    assert {e.step for e in events} & {"apply.step.routerboot_current"}


def test_going_down_says_the_bootloader_stays(tmp_path):
    plan = plan_version(mmips("6.49.21"), scan_update_dir(mmips_dir(tmp_path)), V("6.49.12"))
    note = next(i for i in plan.issues if i.code == "version.routerboot_stays")
    assert "6.49.21" in note.message and "Snížit RouterBOOT" in note.message


@pytest.mark.parametrize(("current", "available", "up", "down"), [
    ("6.49.21", "7.23.7", True, False),   # after installing a newer RouterOS
    ("7.23.7", "6.49.21", False, True),   # after a downgrade
    ("7.23.7", "7.23.7", False, False),   # nothing waiting
    ("7.23.7", "", False, False),         # the board did not say
])
def test_what_the_firmware_state_allows(current, available, up, down):
    state = RouterbootState(True, current, available)
    assert (state.upgradable, state.older) == (up, down)
    assert state.writable() is up
    assert state.writable(allow_older=True) is (up or down)


def test_a_board_that_is_not_a_routerboard_allows_nothing():
    state = RouterbootState(False, "7.23.7", "7.23.9")  # CHR, x86
    assert not state.upgradable and not state.writable(allow_older=True)
