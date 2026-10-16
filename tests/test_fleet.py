"""Updating a fleet: the order, the stop, and the dry run that happens before either.

The asymmetry everything here is built on: a batch that stops too early costs an evening,
and a batch that carries on past a failure costs a site visit.
"""

from __future__ import annotations

from pathlib import Path

from factories import make_inventory, make_radios
from factories import make_npk as npk
from mqs.apply.packages import scan_update_dir
from mqs.discover.candidates import Candidate, Sighting, Source, confirmed, from_sighting
from mqs.discover.topology import build
from mqs.fleet.batch import (
    BatchRunner,
    Outcome,
    dry_run_lines,
    plan_batch,
)
from mqs.model.inventory import PackageInfo
from mqs.model.version import RosVersion

V = RosVersion.parse
MB = 1024 * 1024


def arm_dir(tmp_path: Path, *versions: str) -> Path:
    folder = tmp_path / "Update"
    folder.mkdir(parents=True, exist_ok=True)
    for text in versions:
        version = V(text)
        parts = (version.major, version.minor, version.patch)
        (folder / f"routeros-{text}-arm.npk").write_bytes(npk("system", parts, "arm"))
        (folder / f"wireless-{text}-arm.npk").write_bytes(npk("wireless", parts, "arm"))
    return folder


def device(identity: str, *, mode: str = "station-bridge", version: str = "7.15.3",
           parent: str = "") -> Candidate:
    inventory = make_inventory(
        version, architecture="arm", model="RBSXTsq5nD", free_hdd_space=256 * MB,
        factory_firmware="7.10.0", radios=make_radios(("wlan1", "wireless", mode)),
        packages=[PackageInfo(name="routeros", version=version),
                  PackageInfo(name="wireless", version=version)])
    board = inventory.routerboard.model_copy(update={"serial_number": identity.upper()})
    inventory = inventory.model_copy(update={"identity": identity, "routerboard": board})
    sightings = ([Sighting(source=Source.REGISTRATION, found_at=parent,
                           via_interface="wlan1")] if parent else [])
    return confirmed(Candidate(sightings=sightings), inventory)


def guess(identity: str) -> Candidate:
    return from_sighting(Sighting(source=Source.MNDP, identity=identity,
                                  macs=["AA:BB:CC:00:00:99"]))


def chain() -> list[Candidate]:
    """core <- relay <- leaf, which is the shape that matters."""
    return [device("core", mode="ap-bridge"),
            device("relay", mode="ap-bridge", parent="core"),
            device("leaf", parent="relay")]


def plan_for(tmp_path: Path, devices, target: str = "7.16.1", **extra):
    folder = arm_dir(tmp_path, "7.15.3", target)
    found = scan_update_dir(folder)
    return plan_batch(devices, V(target), lambda d: found, **extra)


# ------------------------------------------------------------------ the order


def test_the_batch_runs_from_the_leaves_inward(tmp_path):
    plan = plan_for(tmp_path, chain())
    assert [s.name for s in plan.steps] == ["leaf", "relay", "core"]
    assert not plan.order.needs_confirmation


def test_each_device_knows_what_is_behind_it(tmp_path):
    """So the message when one fails can say what it just cut off."""
    plan = plan_for(tmp_path, chain())
    core = next(s for s in plan.steps if s.name == "core")
    assert len(core.behind) == 2
    leaf = next(s for s in plan.steps if s.name == "leaf")
    assert leaf.behind == ()


def test_a_device_nobody_logged_into_is_left_out_and_said_so(tmp_path):
    plan = plan_for(tmp_path, [*chain(), guess("maybe")])
    assert "maybe" not in [s.name for s in plan.steps]
    assert "fleet.not_confirmed" in {i.code for i in plan.issues}


def test_an_uncertain_topology_asks_to_be_confirmed(tmp_path):
    """Two devices with no link between them: the order may be right and nothing says so."""
    plan = plan_for(tmp_path, [device("a"), device("b")])
    assert plan.needs_confirmation
    assert "fleet.order_uncertain" in {i.code for i in plan.issues}


def test_a_route_that_is_not_a_plain_upgrade_asks_to_be_confirmed(tmp_path):
    folder = arm_dir(tmp_path, "7.15.3", "7.16.1")
    plan = plan_batch([device("a", version="7.16.1")], V("7.15.3"),
                      lambda d: scan_update_dir(folder),
                      topology=build([device("a", version="7.16.1")]))
    assert plan.needs_confirmation  # a downgrade


# ------------------------------------------------------------------ planning first


def test_every_device_is_planned_before_any_is_touched(tmp_path):
    plan = plan_for(tmp_path, chain())
    assert all(step.plan is not None and step.plan.changes for step in plan.steps)
    assert plan.ready


def test_a_device_that_cannot_be_planned_stops_the_whole_batch(tmp_path):
    """Usually the same missing package for everything on the shelf, and finding that out
    after four reboots helps nobody."""
    folder = tmp_path / "Update"
    folder.mkdir()
    (folder / "routeros-7.16.1-arm.npk").write_bytes(npk("system", (7, 16, 1), "arm"))
    # No `wireless-7.16.1`: every antenna's plan refuses.
    plan = plan_batch(chain(), V("7.16.1"), lambda d: scan_update_dir(folder))
    assert not plan.ready and len(plan.blocked) == 3
    assert all(s.outcome is Outcome.BLOCKED for s in plan.steps)
    assert all("wireless" in s.detail for s in plan.steps)


def test_a_mixed_fleet_gets_the_packages_for_its_own_architecture(tmp_path):
    """One directory's worth of answer is not an answer on a mixed fleet."""
    arm = arm_dir(tmp_path / "arm", "7.15.3", "7.16.1")
    empty = tmp_path / "mmips"
    empty.mkdir()
    mmips = device("mmips-one")
    mmips = mmips.model_copy(update={
        "inventory": mmips.inventory.model_copy(update={
            "resource": mmips.inventory.resource.model_copy(
                update={"architecture": "mmips"})})})
    plan = plan_batch([device("arm-one"), mmips], V("7.16.1"),
                      lambda d: scan_update_dir(arm if d.identity == "arm-one" else empty))
    by_name = {s.name: s for s in plan.steps}
    assert by_name["arm-one"].changes
    assert by_name["mmips-one"].outcome is Outcome.BLOCKED


# ------------------------------------------------------------------ the dry run


def test_a_dry_run_shows_every_device_and_what_it_would_upload(tmp_path):
    lines = dry_run_lines(plan_for(tmp_path, [*chain(), guess("maybe")]))
    text = "\n".join(lines)
    assert "1. leaf (station)" in text
    assert "lowest possible: 7.10" in text
    assert "3. core (AP) via 2 behind" in text
    assert "wireless-7.16.1-arm.npk" in text
    assert "maybe: not confirmed, left out" in text


def test_a_dry_run_says_why_a_device_is_blocked(tmp_path):
    folder = tmp_path / "Update"
    folder.mkdir()
    (folder / "routeros-7.16.1-arm.npk").write_bytes(npk("system", (7, 16, 1), "arm"))
    lines = dry_run_lines(plan_batch([device("a")], V("7.16.1"),
                                     lambda d: scan_update_dir(folder)))
    assert any("wireless" in line for line in lines)


# ------------------------------------------------------------------ running


def test_the_devices_run_in_order(tmp_path):
    plan = plan_for(tmp_path, chain())
    ran: list[str] = []
    report = BatchRunner(plan, run_one=lambda step: ran.append(step.name)).run()
    assert ran == ["leaf", "relay", "core"]
    assert report.succeeded and len(report.of(Outcome.DONE)) == 3


def test_the_batch_stops_at_the_first_failure(tmp_path):
    """A radio that did not come back on one device is the best evidence available that
    the same package set is wrong for the ones behind it."""
    plan = plan_for(tmp_path, chain())
    ran: list[str] = []

    def run_one(step):
        ran.append(step.name)
        if step.name == "relay":
            raise RuntimeError("the radio did not come back")

    report = BatchRunner(plan, run_one=run_one).run()
    assert ran == ["leaf", "relay"]  # core was never touched
    assert not report.succeeded
    assert report.stopped_by is not None and report.stopped_by.name == "relay"
    skipped = report.of(Outcome.SKIPPED)
    assert [s.name for s in skipped] == ["core"]
    assert "stopped at relay" in skipped[0].detail


def test_one_at_a_time_is_the_default(tmp_path):
    plan = plan_for(tmp_path, [device("a"), device("b"), device("c")])
    groups: list[int] = []
    runner = BatchRunner(plan, run_one=lambda step: None)
    groups = [len(g) for g in runner._groups(list(plan.runnable))]
    assert groups == [1, 1, 1]


def test_parallelism_groups_only_devices_with_nothing_behind_them(tmp_path):
    """Two CPEs on a shelf are independent; an AP and the station on it are not, and
    rebooting both at once is how a batch loses the station."""
    devices = [device("core", mode="ap-bridge"),
               device("cpe-a", parent="core"), device("cpe-b", parent="core"),
               device("cpe-c", parent="core")]
    plan = plan_for(tmp_path, devices)
    runner = BatchRunner(plan, run_one=lambda step: None, parallel=2)
    groups = [[s.name for s in g] for g in runner._groups(list(plan.runnable))]
    assert groups == [["cpe-a", "cpe-b"], ["cpe-c"], ["core"]]


def test_parallelism_is_capped(tmp_path):
    devices = [device(f"cpe-{i}") for i in range(12)]
    plan = plan_for(tmp_path, devices)
    runner = BatchRunner(plan, run_one=lambda step: None, parallel=99)
    assert max(len(g) for g in runner._groups(list(plan.runnable))) == 8


def test_a_parallel_group_still_stops_the_batch_when_one_of_it_fails(tmp_path):
    devices = [device("core", mode="ap-bridge"),
               device("cpe-a", parent="core"), device("cpe-b", parent="core")]
    plan = plan_for(tmp_path, devices)

    def run_one(step):
        if step.name == "cpe-b":
            raise RuntimeError("no answer after the reboot")

    report = BatchRunner(plan, run_one=run_one, parallel=2).run()
    assert not report.succeeded
    assert report.stopped_by is not None
    assert [s.name for s in report.of(Outcome.SKIPPED)] == ["core"]


def test_a_cancelled_batch_stops_where_it_was(tmp_path):
    plan = plan_for(tmp_path, chain())
    ran: list[str] = []
    report = BatchRunner(plan, run_one=lambda step: ran.append(step.name),
                         cancelled=lambda: len(ran) >= 1).run()
    assert ran == ["leaf"]
    # Reported in batch order, which is the order they would have run in.
    assert [s.name for s in report.of(Outcome.SKIPPED)] == ["relay", "core"]


def test_every_step_is_reported_as_it_runs(tmp_path):
    plan = plan_for(tmp_path, chain())
    seen: list[tuple[str, Outcome]] = []
    BatchRunner(plan, run_one=lambda step: None,
                on_step=lambda step: seen.append((step.name, step.outcome))).run()
    assert ("leaf", Outcome.RUNNING) in seen and ("leaf", Outcome.DONE) in seen


def test_a_device_with_nothing_to_install_is_not_run(tmp_path):
    """Already on the target version: no reboot, and it does not hold the batch up."""
    plan = plan_for(tmp_path, [device("a", version="7.16.1"), device("b")])
    ran: list[str] = []
    BatchRunner(plan, run_one=lambda step: ran.append(step.name)).run()
    assert ran == ["b"]
