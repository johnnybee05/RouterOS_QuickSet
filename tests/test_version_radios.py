"""Walking a route on a device with radios, and checking they came back.

The one outcome everything here is arranged around: a device that reboots onto the new
version with its radio missing. Catching that needs the ability to cause it, which is what
`FakeRouter.loses_drivers` is for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factories import make_inventory, make_radios
from factories import make_npk as npk
from fake_router import FakeRouter, RouterUploader
from mqs.apply.events import StepStatus
from mqs.apply.packages import scan_update_dir
from mqs.apply.version_change import (
    VersionChangeAborted,
    VersionChanger,
    VersionContext,
    plan_version,
)
from mqs.model.inventory import PackageInfo
from mqs.model.version import RosVersion

V = RosVersion.parse
MB = 1024 * 1024


def write(directory: Path, name: str, data: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(data)
    return directory / name


def arm_dir(tmp_path: Path, *versions: str) -> Path:
    """An Update directory with RouterOS and the legacy driver for each version."""
    folder = tmp_path / "Update"
    for text in versions:
        version = V(text)
        parts = (version.major, version.minor, version.patch)
        write(folder, f"routeros-{text}-arm.npk", npk("system", parts, "arm"))
        write(folder, f"wireless-{text}-arm.npk", npk("wireless", parts, "arm"))
    return folder


def antenna(current: str, *, stations: int = 0, extras: tuple[str, ...] = ()):
    mode = "ap-bridge" if stations else "station-bridge"
    radios = make_radios(("wlan1", "wireless", mode),
                         registrations={"wlan1": stations} if stations else None)
    return make_inventory(
        current, architecture="arm", free_hdd_space=256 * MB, radios=radios,
        factory_firmware="7.10.0", model="RBSXTsq5nD",
        packages=[PackageInfo(name="routeros", version=current),
                  PackageInfo(name="wireless", version=current),
                  *(PackageInfo(name=e, version=current) for e in extras)])


def runner_for(
    tmp_path: Path, target: str, current: str = "7.15.3", *,
    loses: tuple[str, ...] = (), stations: int = 0, reassociate: bool = True,
    registration_timeout: float = 60.0, versions: tuple[str, ...] = (),
    extras: tuple[str, ...] = (),
):
    folder = arm_dir(tmp_path, *(versions or (current, target)))
    inventory = antenna(current, stations=stations, extras=extras)
    # The directory holds RouterOS and the driver and nothing else, so an extra package is
    # one the user has to waive by name — which is the realistic way it gets left behind.
    plan = plan_version(inventory, scan_update_dir(folder), V(target),
                        allow_missing=list(extras))
    assert not plan.blocked, [str(i) for i in plan.issues]

    mode = "ap-bridge" if stations else "station-bridge"
    router = FakeRouter()
    router.version = f"{current} (long-term)"
    router.firmware.update({"current-firmware": "7.10.0", "upgrade-firmware": "7.10.0"})
    router.packages = {"routeros": current, "wireless": current,
                       **{e: current for e in extras}}
    router.radios = {"wireless": [{"name": "wlan1", "mode": mode, "running": "yes"}]}
    router.registrations = {"wireless": [{"interface": "wlan1"}] * stations}
    router.loses_drivers = set(loses)
    router.installable = {p.file_name: str(p.version)
                          for hop in plan.hops for p in hop.packages if p.is_main}
    router.driver_files = {p.file_name: p.name
                           for hop in plan.hops for p in hop.packages if not p.is_main}
    if stations and reassociate:
        # The reboot empties the table and the stations find their way back, which is what
        # the wait is watching for.
        router.stations_after_reboot = [{"interface": "wlan1"}] * stations

    events: list = []
    ctx = VersionContext(
        plan=plan, inventory=inventory, transport=router.session(),
        uploader_factory=lambda transport: RouterUploader(router),
        reconnect=lambda request: router.session(port=request.api_port),
        on_event=events.append, sleep=lambda seconds: None,
        registration_timeout_s=registration_timeout,
    )
    return router, VersionChanger(ctx), events


# ------------------------------------------------------------------ the good case


def test_the_driver_goes_up_with_routeros_and_the_radio_comes_back(tmp_path):
    router, runner, events = runner_for(tmp_path, "7.16.1")
    report = runner.run()
    assert report.succeeded, [c.message for c in report.checks if not c.ok]
    uploaded = sorted(e.params["file"] for e in events
                      if e.step == "apply.step.version_upload" and e.status == StepStatus.OK)
    assert uploaded == ["routeros-7.16.1-arm.npk", "wireless-7.16.1-arm.npk"]
    assert router.packages == {"routeros": "7.16.1", "wireless": "7.16.1"}
    assert [c.code for c in report.checks] == ["verify.routeros_version",
                                               "verify.package_versions", "verify.radios"]


# ------------------------------------------------------------------ the radio that did not


def test_a_radio_that_did_not_come_back_is_a_full_stop(tmp_path):
    """What the whole feature exists to prevent. The device is up and answering, which is
    more than will be true of whatever sits behind it if this is an AP."""
    _router, runner, _events = runner_for(tmp_path, "7.16.1", loses=("wireless",))
    with pytest.raises(VersionChangeAborted) as caught:
        runner.run()
    assert caught.value.issue.code == "apply.failed.version_radio_lost"
    radios = next(c for c in caught.value.report.checks if c.code == "verify.radios")
    assert not radios.ok
    assert radios.params["lost"] == "wlan1"
    assert radios.params["menus_lost"] == "wireless"
    assert "wlan1" in caught.value.issue.message


def test_the_radios_are_checked_after_every_hop_not_only_the_last(tmp_path):
    """A radio lost at an intermediate stop is lost before the route is half done, and the
    next reboot would only put it further out of reach."""
    _router, runner, _events = runner_for(
        tmp_path, "7.16.1", current="7.10.2", loses=("wireless",),
        versions=("7.10.2", "7.12.4", "7.16.1"))
    assert [str(h.version) for h in runner.ctx.plan.hops] == ["7.12.4", "7.16.1"]
    with pytest.raises(VersionChangeAborted):
        runner.run()
    # Stopped at the 7.12 stop: one reboot, not two.
    assert runner.report.installed == [V("7.12.4")]


def test_a_radio_that_came_back_down_is_reported(tmp_path):
    """The driver loaded and the radio did not come up: the link is still down."""
    router, runner, _events = runner_for(tmp_path, "7.16.1")
    original = router._install_drivers

    def quiet(installing, *, downgrade):
        original(installing, downgrade=downgrade)
        router.radios["wireless"] = [{"name": "wlan1", "mode": "station-bridge",
                                      "running": "no"}]

    router._install_drivers = quiet
    with pytest.raises(VersionChangeAborted) as caught:
        runner.run()
    radios = next(c for c in caught.value.report.checks if c.code == "verify.radios")
    assert radios.params["stopped"] == "wlan1" and radios.params["lost"] == "-"


# ------------------------------------------------------------------ package parity


def test_a_package_left_on_the_old_version_is_a_warning_not_a_stop(tmp_path):
    """The user waived `gps` to let the update through, and this is what that cost: the
    radio is there, so the link holds and there is something to fix it from."""
    _router, runner, _events = runner_for(tmp_path, "7.16.1", extras=("gps",))
    report = runner.run()
    parity = next(c for c in report.checks if c.code == "verify.package_versions")
    assert not parity.ok and "gps 7.15.3" in parity.params["wrong"]
    assert "apply.warning.version_package_versions" in {i.code for i in report.issues}
    assert next(c for c in report.checks if c.code == "verify.radios").ok


# ------------------------------------------------------------------ the stations


def test_the_stations_of_an_ap_are_waited_for(tmp_path):
    _router, runner, events = runner_for(tmp_path, "7.16.1", stations=3)
    report = runner.run()
    assert report.succeeded, [c.message for c in report.checks if not c.ok]
    check = next(c for c in report.checks if c.code == "verify.registrations")
    assert check.ok and check.params["radios"] == "wlan1"
    assert "apply.step.registrations_wait" in {e.step for e in events}


def test_an_ap_whose_stations_stay_away_fails_the_run(tmp_path):
    """The radio is up, so whatever is wrong is on the far end — but it is still a failed
    verification, and a batch stops on one."""
    _router, runner, _events = runner_for(tmp_path, "7.16.1", stations=2,
                                          reassociate=False, registration_timeout=0.0)
    report = runner.run()
    check = next(c for c in report.checks if c.code == "verify.registrations")
    assert not check.ok and check.params["empty"] == "wlan1"
    assert "apply.warning.version_registrations" in {i.code for i in report.issues}
    assert not report.succeeded


def test_a_station_is_not_waited_for_at_all(tmp_path):
    """Nothing was registered on it before the reboot, so there is nothing to come back
    and waiting would wait out the whole timeout for no reason."""
    _router, runner, events = runner_for(tmp_path, "7.16.1")
    report = runner.run()
    assert "verify.registrations" not in {c.code for c in report.checks}
    assert "apply.step.registrations_wait" not in {e.step for e in events}
    assert report.succeeded


def test_the_wait_can_be_turned_off(tmp_path):
    _router, runner, _events = runner_for(tmp_path, "7.16.1", stations=2,
                                          reassociate=False)
    runner.ctx.wait_for_stations = False
    report = runner.run()
    assert report.succeeded
    assert "verify.registrations" not in {c.code for c in report.checks}


def test_the_stations_are_waited_for_only_after_the_last_hop(tmp_path):
    """Between hops the device is about to reboot again, so waiting for stations that are
    about to be dropped is time spent for nothing."""
    _router, runner, _events = runner_for(tmp_path, "7.16.1", current="7.10.2", stations=2,
                                          versions=("7.10.2", "7.12.4", "7.16.1"))
    report = runner.run()
    assert report.succeeded, [c.message for c in report.checks if not c.ok]
    assert [c.code for c in report.checks].count("verify.registrations") == 1
