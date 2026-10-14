"""Reading back what an update left: package versions, radios, stations."""

from __future__ import annotations

from factories import make_radios
from fake_transport import FakeTransport
from mqs.apply.verify import (
    check_package_versions,
    compare_radios,
    registrations_returned,
    wait_for_radios,
    wait_for_registrations,
)
from mqs.model.inventory import WirelessSnapshot
from mqs.model.version import RosVersion

V = RosVersion.parse


def packages(*rows: dict[str, str]) -> FakeTransport:
    return FakeTransport({"/system/package/print": list(rows)})


# ------------------------------------------------------------------ package parity


def test_every_package_on_the_same_version_passes():
    fake = packages({"name": "routeros", "version": "7.16.1"},
                    {"name": "wireless", "version": "7.16.1"})
    assert check_package_versions(fake, V("7.16.1")).ok


def test_a_driver_left_on_the_old_version_is_caught():
    """The state that costs a radio, and RouterOS does not complain about it."""
    fake = packages({"name": "routeros", "version": "7.16.1"},
                    {"name": "wireless", "version": "7.15.3"})
    check = check_package_versions(fake, V("7.16.1"))
    assert not check.ok and check.params["wrong"] == "wireless 7.15.3"


def test_a_package_scheduled_for_a_reboot_is_caught():
    """The same problem one reboot away."""
    fake = packages({"name": "routeros", "version": "7.16.1"},
                    {"name": "wireless", "version": "7.16.1", "scheduled": "uninstall"})
    check = check_package_versions(fake, V("7.16.1"))
    assert not check.ok and check.params["waiting"] == "wireless (uninstall)"


def test_a_disabled_package_is_caught():
    fake = packages({"name": "routeros", "version": "7.16.1"},
                    {"name": "wireless", "version": "7.16.1", "disabled": "yes"})
    check = check_package_versions(fake, V("7.16.1"))
    assert not check.ok and check.params["waiting"] == "wireless (disabled)"


def test_a_package_that_was_disabled_before_the_update_is_not_its_doing():
    """The v6 hEX S, whose profile switched `ipv6` off: the update leaves it as it was,
    and the check must not fail every hop over it (2026-10-12)."""
    fake = packages({"name": "routeros-mmips", "version": "6.49.22"},
                    {"name": "ipv6", "version": "6.49.22", "disabled": "yes",
                     "bundle": "routeros-mmips"},
                    {"name": "wireless", "version": "6.49.22", "disabled": "yes",
                     "bundle": "routeros-mmips"})
    check = check_package_versions(fake, V("6.49.22"),
                                   enabled_before={"routeros-mmips", "wireless"})
    assert not check.ok and check.params["waiting"] == "wireless (disabled)"
    assert check_package_versions(fake, V("6.49.22"), enabled_before={"routeros-mmips"}).ok


def test_a_package_v7_did_not_have_is_left_as_the_downgrade_brings_it_back():
    """7.23.8 -> 6.49.12 on the same hEX S: v7 has no `ipv6` package of its own, and the
    downgrade brings back v6's, disabled as the profile left it (2026-10-12)."""
    fake = packages({"name": "routeros-mmips", "version": "6.49.12"},
                    {"name": "ipv6", "version": "6.49.12", "disabled": "yes",
                     "bundle": "routeros-mmips"},
                    {"name": "system", "version": "6.49.12", "bundle": "routeros-mmips"})
    before_on_v7 = {"routeros"}  # one package, enabled
    assert check_package_versions(fake, V("6.49.12"), enabled_before=before_on_v7).ok


def test_packages_only_offered_for_download_are_not_ours_to_check():
    """7.23 lists every package MikroTik publishes for the board; the ones with flag `A`
    are not installed and have no version to be wrong."""
    fake = packages({"name": "routeros", "version": "7.16.1"},
                    {"_flags": "XA", "name": "wifi-qcom", "version": ""},
                    {"name": "container", "version": "", "available": "yes"})
    assert check_package_versions(fake, V("7.16.1")).ok


def test_a_v6_bundle_and_its_members_all_count():
    fake = packages({"name": "routeros-mmips", "version": "6.49.22"},
                    {"name": "wireless", "version": "6.49.22", "bundle": "routeros-mmips"},
                    {"name": "ipv6", "version": "6.49.12", "bundle": "routeros-mmips"})
    check = check_package_versions(fake, V("6.49.22"))
    assert not check.ok and check.params["wrong"] == "ipv6 6.49.12"


# ------------------------------------------------------------------ the radios


def test_the_same_radios_pass():
    before = make_radios(("wlan1", "wireless", "ap-bridge"))
    assert compare_radios(before, before).ok


def test_a_menu_that_is_gone_means_the_driver_did_not_come_back():
    before = make_radios(("wlan1", "wireless", "ap-bridge"))
    check = compare_radios(before, WirelessSnapshot(collected=True))
    assert not check.ok
    assert check.params["menus_lost"] == "wireless" and check.params["lost"] == "wlan1"


def test_an_interface_that_is_gone_means_the_driver_loaded_and_the_radio_did_not():
    before = make_radios(("wlan1", "wireless", "ap-bridge"),
                         ("wlan2", "wireless", "ap-bridge"))
    after = make_radios(("wlan1", "wireless", "ap-bridge"))
    check = compare_radios(before, after)
    assert not check.ok
    assert check.params["menus_lost"] == "-" and check.params["lost"] == "wlan2"


def test_a_radio_that_is_no_longer_running_means_the_link_is_down():
    before = make_radios(("wlan1", "wireless", "station-bridge"))
    after = WirelessSnapshot(collected=True, menus=before.menus,
                             interfaces=[before.interfaces[0].model_copy(
                                 update={"running": False})])
    check = compare_radios(before, after)
    assert not check.ok and check.params["stopped"] == "wlan1"


def _down(snapshot: WirelessSnapshot, *names: str) -> WirelessSnapshot:
    return snapshot.model_copy(update={"interfaces": [
        i.model_copy(update={"running": False}) if i.name in names else i
        for i in snapshot.interfaces]})


def test_an_ap_on_the_wifi_driver_without_stations_is_not_a_radio_down():
    """The hAP ac^3 going 7.23.6 -> 7.24.5 (2026-10-14): `wifi2-5Ghz` on a DFS channel had
    nobody back yet when the check ran, and under `/interface wifi` an AP's R flag only
    says somebody is registered. The stations are the registration wait's business."""
    before = make_radios(("wifi1", "wifi", "ap"), ("wifi2", "wifi", "ap"),
                         registrations={"wifi2": 3})
    check = compare_radios(before, _down(before, "wifi1", "wifi2"))
    assert check.ok and check.params["stopped"] == "-"


def test_a_station_on_the_wifi_driver_that_is_not_running_has_no_link():
    before = make_radios(("wifi1", "wifi", "station"))
    check = compare_radios(before, _down(before, "wifi1"))
    assert not check.ok and check.params["stopped"] == "wifi1"


def test_a_radio_that_was_already_down_is_not_this_flows_business():
    before = make_radios(("wlan1", "wireless", "station-bridge"))
    down = before.model_copy(update={
        "interfaces": [before.interfaces[0].model_copy(update={"running": False})]})
    assert compare_radios(down, down).ok


def test_a_new_radio_is_named_but_is_not_a_failure():
    """A driver that renames or splits a radio across a version has not lost one — but
    somebody has to look at the configuration afterwards."""
    before = make_radios(("wlan1", "wireless", "ap-bridge"))
    after = make_radios(("wlan1", "wireless", "ap-bridge"),
                        ("wlan2", "wireless", "ap-bridge"))
    check = compare_radios(before, after)
    assert check.ok and check.params["new"] == "wlan2"


def test_a_device_with_no_radio_compares_clean():
    empty = WirelessSnapshot(collected=True)
    assert compare_radios(empty, empty).ok


def _rebooting(states: list[str]) -> FakeTransport:
    """A station whose `running` changes between reads, as a radio coming up does."""
    fake = FakeTransport({"/interface/wireless/print": [],
                          "/interface/wireless/registration-table/print": []})
    original = fake.execute
    rounds = {"n": 0}

    def execute(path: str, /, **args: object):
        if path == "/interface/wireless/print":
            state = states[min(rounds["n"], len(states) - 1)]
            rounds["n"] += 1
            if state == "gone":
                return []
            return [{"name": "wlan1", "mode": "station-bridge", "running": state}]
        return original(path, **args)

    fake.execute = execute  # type: ignore[method-assign]
    return fake


def _clock():
    clock = {"t": 0.0}

    def sleep(seconds: float) -> None:
        clock["t"] += seconds

    return clock, sleep


def test_a_radio_that_is_not_up_yet_is_asked_again():
    """The device answers before the radio is up: a DFS channel listens for radar first,
    and a station has to find its AP."""
    before = make_radios(("wlan1", "wireless", "station-bridge"))
    clock, sleep = _clock()
    seen: list[str] = []
    check = wait_for_radios(_rebooting(["no", "no", "yes"]), before, V("7.16.1"),
                            sleep=sleep, timeout_s=660.0, interval_s=10.0,
                            now=lambda: clock["t"], on_wait=seen.append)
    assert check.ok
    assert clock["t"] == 20.0 and seen == ["wlan1", "wlan1"]


def test_a_radio_that_stays_down_fails_at_the_timeout():
    before = make_radios(("wlan1", "wireless", "station-bridge"))
    clock, sleep = _clock()
    check = wait_for_radios(_rebooting(["no"]), before, V("7.16.1"), sleep=sleep,
                            timeout_s=60.0, interval_s=10.0, now=lambda: clock["t"])
    assert not check.ok and check.params["stopped"] == "wlan1"
    assert clock["t"] == 60.0


def test_a_lost_radio_is_not_waited_for():
    """A missing interface is the driver, and no amount of waiting brings it back."""
    before = make_radios(("wlan1", "wireless", "station-bridge"))
    clock, sleep = _clock()
    check = wait_for_radios(_rebooting(["gone", "yes"]), before, V("7.16.1"), sleep=sleep,
                            timeout_s=660.0, now=lambda: clock["t"])
    assert not check.ok and check.params["lost"] == "wlan1"
    assert clock["t"] == 0.0


# ------------------------------------------------------------------ the stations


def test_only_radios_that_had_stations_are_asked_about():
    """An AP with nobody on it had an empty table to begin with, and waiting for it to
    fill would wait forever."""
    before = make_radios(("wlan1", "wireless", "ap-bridge"),
                         ("wlan2", "wireless", "ap-bridge"),
                         registrations={"wlan1": 4, "wlan2": 0})
    after = make_radios(("wlan1", "wireless", "ap-bridge"),
                        ("wlan2", "wireless", "ap-bridge"),
                        registrations={"wlan1": 1})
    ok, empty = registrations_returned(before, after)
    assert ok and not empty


def test_a_radio_that_had_stations_and_has_none_is_reported():
    before = make_radios(("wlan1", "wireless", "ap-bridge"), registrations={"wlan1": 4})
    after = make_radios(("wlan1", "wireless", "ap-bridge"))
    ok, empty = registrations_returned(before, after)
    assert not ok and empty == ["wlan1"]


def _polling(tables: list[list[dict[str, str]]]) -> FakeTransport:
    """A device whose registration table changes between reads."""
    fake = FakeTransport({
        "/interface/wireless/print": [{"name": "wlan1", "mode": "ap-bridge",
                                       "running": "yes"}],
        "/interface/wireless/registration-table/print": [],
    })
    original = fake.execute
    rounds = {"n": 0}

    def execute(path: str, /, **args: object):
        if path == "/interface/wireless/registration-table/print":
            index = min(rounds["n"], len(tables) - 1)
            rounds["n"] += 1
            return [dict(r) for r in tables[index]]
        return original(path, **args)

    fake.execute = execute  # type: ignore[method-assign]
    return fake


def test_the_table_is_read_again_until_the_stations_are_back():
    """A station re-associates on its own schedule, later than the device takes to boot,
    so this is asked repeatedly rather than once."""
    before = make_radios(("wlan1", "wireless", "ap-bridge"), registrations={"wlan1": 2})
    fake = _polling([[], [], [{"interface": "wlan1"}, {"interface": "wlan1"}]])
    slept: list[float] = []
    clock = {"t": 0.0}
    check = wait_for_registrations(fake, before, V("7.16.1"), sleep=slept.append,
                                   timeout_s=300.0, interval_s=15.0,
                                   now=lambda: clock["t"])
    assert check is not None and check.ok
    assert slept == [15.0, 15.0]  # two rounds of waiting, then they were there


def test_the_wait_gives_up_at_the_timeout():
    before = make_radios(("wlan1", "wireless", "ap-bridge"), registrations={"wlan1": 2})
    fake = _polling([[]])
    clock = {"t": 0.0}

    def sleep(seconds: float) -> None:
        clock["t"] += seconds

    check = wait_for_registrations(fake, before, V("7.16.1"), sleep=sleep,
                                   timeout_s=30.0, interval_s=15.0,
                                   now=lambda: clock["t"])
    assert check is not None and not check.ok
    assert check.params["empty"] == "wlan1" and check.params["waited"] == "30"


def test_nothing_is_waited_for_when_no_radio_had_a_station():
    before = make_radios(("wlan1", "wireless", "station-bridge"))
    fake = _polling([[]])
    assert wait_for_registrations(fake, before, V("7.16.1"), sleep=lambda s: None,
                                  timeout_s=300.0) is None


def test_progress_is_reported_while_waiting():
    before = make_radios(("wlan1", "wireless", "ap-bridge"),
                         ("wlan2", "wireless", "ap-bridge"),
                         registrations={"wlan1": 1, "wlan2": 1})
    fake = _polling([[{"interface": "wlan1"}],
                     [{"interface": "wlan1"}, {"interface": "wlan2"}]])
    seen: list[tuple[int, int]] = []
    clock = {"t": 0.0}
    check = wait_for_registrations(
        fake, before, V("7.16.1"), sleep=lambda s: None, timeout_s=300.0,
        now=lambda: clock["t"], on_wait=lambda back, want: seen.append((back, want)))
    assert check is not None and check.ok
    assert seen == [(1, 2), (2, 2)]
