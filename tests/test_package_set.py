"""The package set a wireless device must end up with, and the stops on the way there.

The point of every test here is a radio that is still there afterwards. A plan that
installs RouterOS and leaves the driver behind is not a worse plan, it is a lost link and
— on the far side of a PtP hop — a lost way back in, so the expected answer to anything
uncertain is a refusal with a reason.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factories import make_inventory, make_npk, make_radios
from mqs.apply.package_set import (
    DriverPlan,
    PackageRules,
    choose_wifi_driver,
    default_rules,
    driver_packages,
    driver_switch_issue,
    installed_drivers,
    merged_rules,
    offered_drivers,
    plain_extras,
    plan_route,
)
from mqs.apply.packages import scan_update_dir
from mqs.apply.version_change import plan_version
from mqs.issues import codes
from mqs.model.inventory import (
    PackageInfo,
    WirelessDriver,
    WirelessMenu,
    WirelessSnapshot,
)
from mqs.model.version import RosVersion

V = RosVersion.parse
ARM = "arm"
MB = 1024 * 1024


# ------------------------------------------------------------------ an Update directory


def write(directory: Path, name: str, data: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_bytes(data)
    return directory / name


def arm_dir(tmp_path: Path, *entries: tuple[str, str]) -> Path:
    """``entries`` are (package name, version); the file names follow MikroTik's own, with
    the v6 bundle naming its architecture in the middle."""
    folder = tmp_path / "Update"
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in entries:
        version = V(text)
        parts = (version.major, version.minor, version.patch)
        if version.major == 6 and name == "routeros":
            write(folder, f"routeros-{ARM}-{text}.npk",
                  make_npk(f"routeros-{ARM}", parts, ARM, bundle=True))
        elif name == "routeros":
            write(folder, f"routeros-{text}-{ARM}.npk", make_npk("system", parts, ARM))
        else:
            write(folder, f"{name}-{text}-{ARM}.npk", make_npk(name, parts, ARM))
    return folder


def antenna(
    version: str,
    *,
    packages: tuple[PackageInfo, ...] = (),
    radios: WirelessSnapshot | None = None,
    model: str = "RBSXTsq5nD",
    space: int = 256 * MB,
    **extra: object,
):
    """A wireless device: legacy radio on /interface wireless unless said otherwise."""
    return make_inventory(
        version, architecture=ARM, board_name="SXTsq 5 nD", model=model,
        free_hdd_space=space, free_memory=space, packages=list(packages),
        radios=radios if radios is not None else make_radios(
            ("wlan1", "wireless", "station-bridge")),
        **extra,  # type: ignore[arg-type]
    )


def plan(inventory, folder: Path, target: str, **options):
    return plan_version(inventory, scan_update_dir(folder), V(target), **options)


def files_of(plan_result) -> list[list[str]]:
    return [sorted(p.name for p in hop.packages) for hop in plan_result.hops]


# ------------------------------------------------------------------ reading the device


def test_a_v6_device_has_the_legacy_driver_inside_its_bundle():
    """v6 lists the bundle and its members, never a `wireless` package of its own, so the
    menu is the only evidence the driver is there — and it is enough."""
    inventory = antenna("6.48.6", packages=(
        PackageInfo(name="routeros-arm", version="6.48.6"),
        PackageInfo(name="system", version="6.48.6", bundle="routeros-arm"),
        PackageInfo(name="wireless", version="6.48.6", bundle="routeros-arm"),
    ))
    state = installed_drivers(inventory)
    assert state.drivers == frozenset({WirelessDriver.WIRELESS})
    assert state.bundled == frozenset({WirelessDriver.WIRELESS})
    assert not state.unexplained


def test_a_v7_device_is_read_from_its_package_list():
    inventory = antenna("7.15.3", packages=(
        PackageInfo(name="routeros", version="7.15.3"),
        PackageInfo(name="wireless", version="7.15.3"),
    ))
    assert installed_drivers(inventory).drivers == frozenset({WirelessDriver.WIRELESS})
    assert not installed_drivers(inventory).bundled


def test_sixty_gigahertz_counts_as_the_legacy_driver():
    """`/interface w60g` is served by the `wireless` package, so a Wireless Wire needs it
    exactly as a legacy radio does."""
    inventory = antenna("7.15.3", radios=make_radios(("wlan60-1", "w60g", "bridge")))
    assert installed_drivers(inventory).drivers == frozenset({WirelessDriver.WIRELESS})


def test_a_wifi_menu_with_no_driver_package_is_not_guessed_at():
    """Three packages can serve /interface wifi. A device that answers there and names
    none of them has not told us what to upload, and that is a refusal."""
    inventory = antenna("7.15.3", radios=make_radios(("wifi1", "wifi", "ap")),
                        packages=(PackageInfo(name="routeros", version="7.15.3"),))
    state = installed_drivers(inventory)
    assert state.unexplained == frozenset({WirelessMenu.WIFI})
    plan = driver_packages(state, V("7.16.1"), inventory)
    assert not plan.required and codes(plan.issues) == {"version.driver_unknown"}


def test_a_device_with_no_radio_needs_no_driver():
    plain = make_inventory("7.15.3", architecture=ARM)
    state = installed_drivers(plain)
    assert not state.any_wireless
    assert driver_packages(state, V("7.16.1"), plain) == DriverPlan()


# ------------------------------------------------------------------ naming the driver


@pytest.mark.parametrize("installed, target, expected", [
    # legacy wireless keeps its name in every direction
    ("wireless", "7.16.1", {"wireless"}),
    ("wireless", "7.10.2", {"wireless"}),
    # 7.13 split: going down, both halves collapse onto the one old name
    ("wifi-qcom", "7.12.4", {"wifiwave2"}),
    ("wifi-qcom-ac", "7.12.4", {"wifiwave2"}),
    # ... and above the split each keeps its own, never the other
    ("wifi-qcom", "7.16.1", {"wifi-qcom"}),
    ("wifi-qcom-ac", "7.16.1", {"wifi-qcom-ac"}),
    # below the split wifiwave2 is still wifiwave2
    ("wifiwave2", "7.12.4", {"wifiwave2"}),
])
def test_a_driver_is_named_the_way_the_target_version_names_it(installed, target, expected):
    inventory = antenna("7.15.3", radios=make_radios(("wifi1", "wifi", "ap"))
                        if installed != "wireless" else None,
                        packages=(PackageInfo(name="routeros", version="7.15.3"),
                                  PackageInfo(name=installed, version="7.15.3")))
    plan = driver_packages(installed_drivers(inventory), V(target), inventory)
    assert plan.required == expected and not plan.issues


def test_wifiwave2_going_to_713_takes_the_answer_from_the_device_itself():
    """7.23 lists every package MikroTik publishes for the board with `available=true`;
    a board that offers one half of the split has answered which driver belongs on it."""
    inventory = antenna("7.12.4", radios=make_radios(("wifi1", "wifi", "ap")),
                        model="unknown-board", packages=(
        PackageInfo(name="routeros", version="7.12.4"),
        PackageInfo(name="wifiwave2", version="7.12.4"),
        PackageInfo(name="wifi-qcom-ac", available=True, disabled=True),
    ))
    assert offered_drivers(inventory) == frozenset({WirelessDriver.WIFI_QCOM_AC})
    plan = driver_packages(installed_drivers(inventory), V("7.16.1"), inventory)
    assert plan.required == {"wifi-qcom-ac"} and not plan.issues


def test_an_ax_board_is_recognised_by_its_radio_designator():
    """MikroTik's own naming: an 802.11ax board's designator carries `ax`
    (C52iG-5HaxD2HaxD), a Wave2 board's does not (RBD53iG-5HacD2HnD)."""
    ax = antenna("7.12.4", radios=make_radios(("wifi1", "wifi", "ap")),
                 model="C52iG-5HaxD2HaxD", packages=(
                     PackageInfo(name="routeros", version="7.12.4"),
                     PackageInfo(name="wifiwave2", version="7.12.4")))
    plan = driver_packages(installed_drivers(ax), V("7.16.1"), ax)
    assert plan.required == {"wifi-qcom"} and not plan.issues


def test_an_unknown_board_on_wifiwave2_is_a_question_not_a_default():
    """The dangerous case: nothing says which half of the split this board needs. Picking
    the more common one would wipe the interface configuration when it is wrong."""
    unknown = antenna("7.12.4", radios=make_radios(("wifi1", "wifi", "ap")),
                      model="RBD53iG-5HacD2HnD-XX", packages=(
                          PackageInfo(name="routeros", version="7.12.4"),
                          PackageInfo(name="wifiwave2", version="7.12.4")))
    plan = driver_packages(installed_drivers(unknown), V("7.16.1"), unknown)
    assert not plan.required and codes(plan.issues) == {"version.wifi_choice_needed"}
    # ... and the user's answer is taken.
    answered = driver_packages(installed_drivers(unknown), V("7.16.1"), unknown,
                               wifi_override=WirelessDriver.WIFI_QCOM_AC)
    assert answered.required == {"wifi-qcom-ac"} and not answered.issues


def test_the_choice_cannot_be_used_to_switch_drivers():
    """Naming a wifi driver on a device whose radios run the legacy one is not a choice,
    it is a driver change — and that empties the interface configuration."""
    legacy = antenna("7.15.3", packages=(PackageInfo(name="routeros", version="7.15.3"),
                                         PackageInfo(name="wireless", version="7.15.3")))
    state = installed_drivers(legacy)
    problem = driver_switch_issue(state, WirelessDriver.WIFI_QCOM_AC)
    assert problem is not None and problem.code == "version.driver_switch"
    assert driver_switch_issue(state, None) is None


def test_a_wifi_device_has_nowhere_to_go_on_v6():
    """v6 has no `wifi` driver at all, so the radios would come back with nothing driving
    them. The board floor usually catches this; a board that does not say must not slip
    through."""
    inventory = antenna("7.15.3", radios=make_radios(("wifi1", "wifi", "ap")),
                        packages=(PackageInfo(name="routeros", version="7.15.3"),
                                  PackageInfo(name="wifi-qcom", version="7.15.3")))
    plan = driver_packages(installed_drivers(inventory), V("6.49.22"), inventory)
    assert not plan.required and codes(plan.issues) == {"version.driver_no_v6"}


def test_a_v6_target_needs_no_driver_file_for_a_legacy_radio():
    """Going back to v6 the bundle carries the driver, so nothing extra is asked for."""
    inventory = antenna("7.15.3", packages=(PackageInfo(name="routeros", version="7.15.3"),
                                            PackageInfo(name="wireless", version="7.15.3")))
    assert driver_packages(installed_drivers(inventory), V("6.49.22"),
                           inventory) == DriverPlan()


def test_an_explicit_choice_outside_the_split_is_refused():
    inventory = antenna("7.12.4", radios=make_radios(("wifi1", "wifi", "ap")),
                        packages=(PackageInfo(name="wifiwave2", version="7.12.4"),))
    chosen, problem = choose_wifi_driver(
        [WirelessDriver.WIFI_QCOM, WirelessDriver.WIFI_QCOM_AC], inventory,
        override=WirelessDriver.WIRELESS)
    assert chosen is None and problem is not None
    assert problem.code == "version.wifi_choice_invalid"


# ------------------------------------------------------------------ the route


@pytest.mark.parametrize("current, target, available, expected", [
    # nothing in the way
    ("7.15.3", "7.16.1", ["7.15.3", "7.16.1"], ["7.16.1"]),
    # the 7.12 conversion stop, on the way up to the split and no further down
    ("7.10.2", "7.16.1", ["7.10.2", "7.12.4", "7.16.1"], ["7.12.4", "7.16.1"]),
    ("7.10.2", "7.12.4", ["7.10.2", "7.12.4"], ["7.12.4"]),
    # a downgrade crosses the split the other way and needs no conversion stop
    ("7.16.1", "7.10.2", ["7.10.2", "7.12.4", "7.16.1"], ["7.10.2"]),
    # the v6 gate alone
    ("6.48.6", "6.49.22", ["6.48.6", "6.49.22"], ["6.49.22"]),
    # both gates, composed: v6 -> 6.49.22 -> 7.12.x -> target
    ("6.48.6", "7.16.1", ["6.48.6", "6.49.22", "7.12.4", "7.16.1"],
     ["6.49.22", "7.12.4", "7.16.1"]),
    # already past the v6 gate, so only the 7.12 one is left
    ("6.49.22", "7.16.1", ["6.49.22", "7.12.4", "7.16.1"], ["7.12.4", "7.16.1"]),
    # the newest version inside a gate's range is the one picked
    ("7.10.2", "7.16.1", ["7.12.0", "7.12.4", "7.16.1"], ["7.12.4", "7.16.1"]),
])
def test_the_route_collects_the_stops_it_needs(current, target, available, expected):
    route = plan_route(V(current), V(target), [V(v) for v in available])
    assert not route.blocked
    assert [str(v) for v in route.stops] == expected


def test_the_712_stop_is_only_for_devices_with_a_radio():
    """What the stop converts is the wireless packages. A device without them has nothing
    to convert, which is why the mmips hEX S crossed 6.49.12 -> 6.49.22 -> 7.23.7 in two
    hops and was right to (OVĚŘENO 2026-09-28). The same route on an antenna takes three.
    """
    available = [V(v) for v in ("6.49.12", "6.49.22", "7.23.5")]
    no_radio = plan_route(V("6.49.12"), V("7.23.5"), available, has_drivers=False)
    assert not no_radio.blocked
    assert [str(v) for v in no_radio.stops] == ["6.49.22", "7.23.5"]

    # The same directory is not enough for an antenna: there is no 7.12.x in it.
    radio = plan_route(V("6.49.12"), V("7.23.5"), available, has_drivers=True)
    assert radio.blocked and codes(radio.issues) == {"version.needs_712"}


def test_the_v6_gate_applies_to_every_device():
    """MikroTik's 6.49.22 requirement has nothing to do with radios, so a device without
    one does not get to skip it."""
    route = plan_route(V("6.48.6"), V("7.16.1"), [V("6.48.6"), V("7.16.1")],
                       has_drivers=False)
    assert route.blocked and codes(route.issues) == {"version.v7_needs_649"}


def test_a_stop_that_is_not_available_is_a_refusal_not_a_jump():
    route = plan_route(V("7.10.2"), V("7.16.1"), [V("7.10.2"), V("7.16.1")])
    assert route.blocked and codes(route.issues) == {"version.needs_712"}
    assert not route.stops


def test_the_v6_gate_still_refuses_without_a_last_v6_release():
    route = plan_route(V("6.48.6"), V("7.16.1"), [V("6.48.6"), V("7.16.1")])
    assert route.blocked and codes(route.issues) == {"version.v7_needs_649"}


# ------------------------------------------------------------------ the whole plan


def test_a_v6_antenna_going_to_716_carries_wireless_from_the_first_v7_stop(tmp_path):
    """The case the whole feature exists for. v6 keeps the driver in its bundle, so there
    is nothing in `/system package print` to carry over — and the v7 stops still need
    `wireless`, or the radio comes back with no driver."""
    folder = arm_dir(tmp_path, ("routeros", "6.48.6"), ("routeros", "6.49.22"),
                     ("routeros", "7.12.4"), ("wireless", "7.12.4"),
                     ("routeros", "7.16.1"), ("wireless", "7.16.1"))
    inventory = antenna("6.48.6", packages=(
        PackageInfo(name="routeros-arm", version="6.48.6"),
        PackageInfo(name="wireless", version="6.48.6", bundle="routeros-arm"),
    ))
    result = plan(inventory, folder, "7.16.1")
    assert not result.blocked, [str(i) for i in result.issues]
    assert [str(h.version) for h in result.hops] == ["6.49.22", "7.12.4", "7.16.1"]
    assert files_of(result) == [
        ["routeros-arm"],             # the v6 stop: the bundle carries the driver
        ["system", "wireless"],       # the 7.12 conversion stop
        ["system", "wireless"],       # and the target
    ]
    assert result.drivers == {"wireless"}


def test_an_antenna_is_not_updated_without_the_driver_file(tmp_path):
    """No `wireless` for the target version anywhere: the plan stops here rather than
    uploading RouterOS on its own."""
    folder = arm_dir(tmp_path, ("routeros", "7.15.3"), ("wireless", "7.15.3"),
                     ("routeros", "7.16.1"))
    inventory = antenna("7.15.3", packages=(PackageInfo(name="routeros", version="7.15.3"),
                                            PackageInfo(name="wireless", version="7.15.3")))
    result = plan(inventory, folder, "7.16.1")
    assert result.blocked and "version.driver_missing" in codes(result.issues)


def test_a_driver_file_of_the_wrong_version_does_not_count(tmp_path):
    """`wireless-7.15.3` next to `routeros-7.16.1` is the mismatch that leaves a radio
    with a driver its RouterOS cannot load."""
    folder = arm_dir(tmp_path, ("routeros", "7.16.1"), ("wireless", "7.15.3"))
    inventory = antenna("7.15.3", packages=(PackageInfo(name="routeros", version="7.15.3"),
                                            PackageInfo(name="wireless", version="7.15.3")))
    assert plan(inventory, folder, "7.16.1").blocked


def test_an_ax_device_keeps_wifi_qcom(tmp_path):
    folder = arm_dir(tmp_path, ("routeros", "7.16.1"), ("wifi-qcom", "7.16.1"))
    inventory = antenna("7.15.3", radios=make_radios(("wifi1", "wifi", "ap")),
                        model="C52iG-5HaxD2HaxD", packages=(
                            PackageInfo(name="routeros", version="7.15.3"),
                            PackageInfo(name="wifi-qcom", version="7.15.3")))
    result = plan(inventory, folder, "7.16.1")
    assert not result.blocked and files_of(result) == [["system", "wifi-qcom"]]


def test_a_wave2_device_keeps_wifi_qcom_ac(tmp_path):
    folder = arm_dir(tmp_path, ("routeros", "7.16.1"), ("wifi-qcom-ac", "7.16.1"),
                     ("wifi-qcom", "7.16.1"))
    inventory = antenna("7.15.3", radios=make_radios(("wifi1", "wifi", "ap")),
                        packages=(PackageInfo(name="routeros", version="7.15.3"),
                                  PackageInfo(name="wifi-qcom-ac", version="7.15.3")))
    result = plan(inventory, folder, "7.16.1")
    # Both halves of the split are in the directory; the one the device runs is the one
    # that goes up.
    assert not result.blocked and files_of(result) == [["system", "wifi-qcom-ac"]]


def test_a_sixty_gigahertz_link_carries_wireless(tmp_path):
    folder = arm_dir(tmp_path, ("routeros", "7.16.1"), ("wireless", "7.16.1"))
    inventory = antenna("7.15.3", radios=make_radios(("wlan60-1", "w60g", "bridge")),
                        model="RBwAPG-60ad", packages=(
                            PackageInfo(name="routeros", version="7.15.3"),
                            PackageInfo(name="wireless", version="7.15.3")))
    result = plan(inventory, folder, "7.16.1")
    assert not result.blocked and files_of(result) == [["system", "wireless"]]


def test_an_ordinary_package_blocks_the_update_until_it_is_waived(tmp_path):
    folder = arm_dir(tmp_path, ("routeros", "7.16.1"), ("wireless", "7.16.1"))
    inventory = antenna("7.15.3", packages=(
        PackageInfo(name="routeros", version="7.15.3"),
        PackageInfo(name="wireless", version="7.15.3"),
        PackageInfo(name="gps", version="7.15.3"),
    ))
    blocked = plan(inventory, folder, "7.16.1")
    assert blocked.blocked and "version.extra_missing_blocking" in codes(blocked.issues)

    waived = plan(inventory, folder, "7.16.1", allow_missing=["gps"])
    assert not waived.blocked
    assert "version.extra_missing_allowed" in codes(waived.issues)


def test_a_waiver_never_covers_a_driver(tmp_path):
    """`--allow-missing-package wireless` is not a thing: the waiver is for packages whose
    absence costs a feature, not for the one whose absence costs the link."""
    folder = arm_dir(tmp_path, ("routeros", "7.16.1"))
    inventory = antenna("7.15.3", packages=(PackageInfo(name="routeros", version="7.15.3"),
                                            PackageInfo(name="wireless", version="7.15.3")))
    result = plan(inventory, folder, "7.16.1", allow_missing=["wireless"])
    assert result.blocked and "version.driver_missing" in codes(result.issues)


def test_a_small_antenna_without_room_is_refused(tmp_path):
    """16 MB of flash, and the packages do not fit. Nothing is uploaded and nothing of the
    user's is deleted to make room."""
    folder = arm_dir(tmp_path, ("routeros", "7.16.1"), ("wireless", "7.16.1"))
    inventory = antenna("7.15.3", space=200, packages=(
        PackageInfo(name="routeros", version="7.15.3"),
        PackageInfo(name="wireless", version="7.15.3")))
    result = plan(inventory, folder, "7.16.1")
    assert result.blocked and "version.no_space" in codes(result.issues)


def test_a_downgrade_still_needs_confirming(tmp_path):
    folder = arm_dir(tmp_path, ("routeros", "7.15.3"), ("wireless", "7.15.3"),
                     ("routeros", "7.16.1"), ("wireless", "7.16.1"))
    inventory = antenna("7.16.1", packages=(PackageInfo(name="routeros", version="7.16.1"),
                                            PackageInfo(name="wireless", version="7.16.1")))
    result = plan(inventory, folder, "7.15.3")
    assert not result.blocked and result.downgrades and result.needs_confirmation


def test_a_target_below_the_factory_version_is_refused(tmp_path):
    folder = arm_dir(tmp_path, ("routeros", "7.10.2"), ("wireless", "7.10.2"),
                     ("routeros", "7.16.1"), ("wireless", "7.16.1"))
    inventory = antenna("7.16.1", factory_firmware="7.15.3", packages=(
        PackageInfo(name="routeros", version="7.16.1"),
        PackageInfo(name="wireless", version="7.16.1")))
    result = plan(inventory, folder, "7.10.2")
    assert result.blocked and "version.below_factory" in codes(result.issues)


def test_the_version_it_already_runs_is_not_a_plan(tmp_path):
    folder = arm_dir(tmp_path, ("routeros", "7.16.1"))
    inventory = antenna("7.16.1")
    result = plan(inventory, folder, "7.16.1")
    assert not result.changes and "version.same" in codes(result.issues)


def test_nothing_is_planned_from_a_snapshot_nobody_took(tmp_path):
    """The failure this guards against is silent: an empty snapshot would make an antenna
    look like a device with no radio, and the driver would simply not be uploaded."""
    folder = arm_dir(tmp_path, ("routeros", "7.16.1"), ("wireless", "7.16.1"))
    inventory = antenna("7.15.3", radios=WirelessSnapshot(),  # never collected
                        packages=(PackageInfo(name="routeros", version="7.15.3"),
                                  PackageInfo(name="wireless", version="7.15.3")))
    result = plan(inventory, folder, "7.16.1")
    assert result.blocked and codes(result.issues) == {"version.no_snapshot"}


def test_a_device_with_no_radio_plans_as_before(tmp_path):
    """The hEX S in the lab has no radio at all; none of this may get in its way."""
    folder = arm_dir(tmp_path, ("routeros", "7.16.1"))
    inventory = make_inventory("7.15.3", architecture=ARM, free_hdd_space=256 * MB,
                               packages=[PackageInfo(name="routeros", version="7.15.3")])
    result = plan(inventory, folder, "7.16.1")
    assert not result.blocked and files_of(result) == [["system"]]


# ------------------------------------------------------------------ the rules file


def test_the_bundled_rules_load():
    rules = default_rules()
    assert rules.driver_names == {"wireless", "wifiwave2", "wifi-qcom", "wifi-qcom-ac"}
    assert WirelessMenu.W60G in rules.menus_of(WirelessDriver.WIRELESS)
    assert len(rules.gates) == 2 and rules.space_reserve > 0


def test_a_user_file_adds_to_the_model_map_without_replacing_the_rest(tmp_path):
    """The point of the file being editable: an answer the application had to ask for
    stays answered, and nothing else moves."""
    override = tmp_path / "wireless_packages.yaml"
    override.write_text(
        "wifi_split:\n"
        "  by_model:\n"
        '    "RBD53iG-5HacD2HnD-XX": wifi-qcom-ac\n',
        encoding="utf-8")
    rules = merged_rules(override)
    assert rules.split.by_model == {"RBD53iG-5HacD2HnD-XX": WirelessDriver.WIFI_QCOM_AC}
    assert rules.driver_names == default_rules().driver_names
    assert len(rules.gates) == 2

    unknown = antenna("7.12.4", radios=make_radios(("wifi1", "wifi", "ap")),
                      model="RBD53iG-5HacD2HnD-XX", packages=(
                          PackageInfo(name="wifiwave2", version="7.12.4"),))
    plan = driver_packages(installed_drivers(unknown, rules), V("7.16.1"),
                           unknown, rules=rules)
    assert plan.required == {"wifi-qcom-ac"} and not plan.issues


def test_a_missing_user_file_is_not_an_error(tmp_path):
    assert merged_rules(tmp_path / "nope.yaml") is default_rules()


def test_the_rules_can_be_built_from_nothing():
    """A planner given empty rules asks for no driver and knows no stop — it must not
    throw, so a broken user file degrades to "no special rules" rather than a crash."""
    empty = PackageRules.from_mapping({})
    assert plan_route(V("6.48.6"), V("7.16.1"), [V("7.16.1")], empty).stops == (V("7.16.1"),)


def test_plain_extras_leave_the_drivers_out():
    inventory = antenna("7.15.3", packages=(
        PackageInfo(name="routeros", version="7.15.3"),
        PackageInfo(name="wireless", version="7.15.3"),
        PackageInfo(name="container", version="7.15.3"),
        PackageInfo(name="gps", version="7.15.3"),
    ))
    assert sorted(p.name for p in plain_extras(inventory)) == ["container", "gps"]
