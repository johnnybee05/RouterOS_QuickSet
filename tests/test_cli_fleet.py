"""`mqs fleet update` from the outside.

What matters at this level: the dry run is honest and free, the order is shown before
anything is asked, and a batch that should not run says so rather than starting.
"""

from __future__ import annotations

import io
import time
from pathlib import Path

from factories import make_inventory, make_radios
from factories import make_npk as npk
from mqs import cli
from mqs.discover.candidates import Candidate, Sighting, Source, confirmed
from mqs.discover.store import Inventory, save
from mqs.model.inventory import PackageInfo

MB = 1024 * 1024


def arm_dir(tmp_path: Path, *versions: str, drivers: bool = True) -> Path:
    folder = tmp_path / "Update"
    folder.mkdir(parents=True, exist_ok=True)
    for text in versions:
        parts = tuple(int(p) for p in [*text.split("."), "0", "0"][:3])
        (folder / f"routeros-{text}-arm.npk").write_bytes(npk("system", parts, "arm"))
        if drivers:
            (folder / f"wireless-{text}-arm.npk").write_bytes(
                npk("wireless", parts, "arm"))
    return folder


def device(identity: str, *, address: str, mode: str = "station-bridge",
           parent: str = "", version: str = "7.15.3") -> Candidate:
    inventory = make_inventory(
        version, architecture="arm", model="RBSXTsq5nD", free_hdd_space=256 * MB,
        factory_firmware="7.10.0", radios=make_radios(("wlan1", "wireless", mode)),
        packages=[PackageInfo(name="routeros", version=version),
                  PackageInfo(name="wireless", version=version)])
    board = inventory.routerboard.model_copy(update={"serial_number": identity.upper()})
    inventory = inventory.model_copy(update={"identity": identity, "routerboard": board})
    sightings = [Sighting(source=Source.MNDP, address=address, identity=identity)]
    if parent:
        sightings.append(Sighting(source=Source.REGISTRATION, found_at=parent,
                                  via_interface="wlan1"))
    return confirmed(Candidate(addresses=[address], sightings=sightings), inventory)


def chain(tmp_path: Path) -> Path:
    """core <- relay <- leaf, written to an inventory file."""
    devices = [device("core", address="192.0.2.1", mode="ap-bridge"),
               device("relay", address="192.0.2.2", mode="ap-bridge", parent="core"),
               device("leaf", address="192.0.2.3", parent="relay")]
    path = tmp_path / "inv.json"
    save(path, Inventory(saved_at=time.time(), devices=devices))
    return path


def run(argv: list[str], **deps) -> tuple[int, str]:
    out = io.StringIO()
    return cli.main(argv, out=out, **deps), out.getvalue()


def fleet_args(tmp_path: Path, inventory: Path, *extra: str) -> list[str]:
    folder = arm_dir(tmp_path, "7.15.3", "7.16.1")
    return ["fleet", "--to", "7.16.1", "--inventory", str(inventory),
            "--update-dir", str(folder), *extra]


# ------------------------------------------------------------------ the dry run


def test_a_dry_run_shows_the_order_and_changes_nothing(tmp_path):
    touched: list[str] = []
    code, out = run(fleet_args(tmp_path, chain(tmp_path), "--dry-run"),
                    update_one=lambda step: touched.append(step.name))
    assert code == cli.OK, out
    assert out.index("leaf") < out.index("relay") < out.index("core")
    assert "wireless-7.16.1-arm.npk" in out
    assert "dry run: nothing was changed on any device" in out
    assert touched == []


def test_a_dry_run_shows_the_tree_it_ordered_by(tmp_path):
    code, out = run(fleet_args(tmp_path, chain(tmp_path), "--dry-run"))
    assert code == cli.OK
    assert "topology:" in out and "core (wlan1) -> relay" in out


def test_a_dry_run_names_what_is_behind_each_device(tmp_path):
    _, out = run(fleet_args(tmp_path, chain(tmp_path), "--dry-run"))
    assert "core (AP) via 2 behind" in out


# ------------------------------------------------------------------ refusing to start


def test_a_batch_without_a_usable_plan_does_not_start(tmp_path):
    """No driver package for the target: the same missing file for all three."""
    folder = arm_dir(tmp_path, "7.15.3", "7.16.1", drivers=False)
    touched: list[str] = []
    code, out = run(["fleet", "--to", "7.16.1", "--inventory", str(chain(tmp_path)),
                     "--update-dir", str(folder), "-y"],
                    update_one=lambda step: touched.append(step.name))
    assert code == cli.BLOCKED and touched == []
    assert "Dávka se nespustí" in out


def test_an_uncertain_order_refuses_until_it_is_confirmed(tmp_path):
    """Two devices and no link between them: the order may be right and nothing says so."""
    path = tmp_path / "inv.json"
    save(path, Inventory(devices=[device("a", address="192.0.2.1"),
                                  device("b", address="192.0.2.2")]))
    touched: list[str] = []
    code, _ = run(fleet_args(tmp_path, path, "-y"),
                  update_one=lambda step: touched.append(step.name))
    assert code == cli.BLOCKED and touched == []

    code, out = run(fleet_args(tmp_path, path, "-y", "--confirm-order"),
                    update_one=lambda step: touched.append(step.name))
    assert code == cli.OK, out
    assert sorted(touched) == ["a", "b"]


def test_an_empty_inventory_says_to_scan_first(tmp_path):
    path = tmp_path / "inv.json"
    save(path, Inventory())
    code, _ = run(fleet_args(tmp_path, path, "-y"))
    assert code == cli.BLOCKED


def test_a_version_that_is_not_a_version_is_refused(tmp_path):
    code, _ = run(["fleet", "--to", "banana", "--inventory", str(chain(tmp_path))])
    assert code == cli.BLOCKED


# ------------------------------------------------------------------ running


def test_the_batch_runs_leaves_first(tmp_path):
    ran: list[str] = []
    code, out = run(fleet_args(tmp_path, chain(tmp_path), "-y", "--confirm-order"),
                    update_one=lambda step: ran.append(step.name))
    assert code == cli.OK, out
    assert ran == ["leaf", "relay", "core"]
    assert "3 device(s) done" in out


def test_the_batch_stops_at_the_first_failure_and_says_what_was_left(tmp_path):
    ran: list[str] = []

    def update(step):
        ran.append(step.name)
        if step.name == "relay":
            raise RuntimeError("the radio did not come back")

    code, out = run(fleet_args(tmp_path, chain(tmp_path), "-y", "--confirm-order"),
                    update_one=update)
    assert code == cli.FAILED
    assert ran == ["leaf", "relay"]  # core was never touched
    assert "Dávka se zastavila na zařízení relay" in out
    assert "1 zařízení se neaktualizovalo" in out


def test_a_failing_device_says_what_it_cut_off(tmp_path):
    def update(step):
        if step.name == "core":
            raise RuntimeError("no answer after the reboot")

    _, out = run(fleet_args(tmp_path, chain(tmp_path), "-y", "--confirm-order"),
                 update_one=update)
    assert "CHYBA core (2 device(s) behind it)" in out


def test_only_limits_the_batch_by_name_or_address(tmp_path):
    inventory = chain(tmp_path)
    ran: list[str] = []
    run(fleet_args(tmp_path, inventory, "-y", "--confirm-order", "--only", "leaf"),
        update_one=lambda step: ran.append(step.name))
    assert ran == ["leaf"]

    ran.clear()
    run(fleet_args(tmp_path, inventory, "-y", "--confirm-order",
                   "--only", "192.0.2.2"),
        update_one=lambda step: ran.append(step.name))
    assert ran == ["relay"]


def test_only_that_matches_nothing_is_refused_rather_than_doing_everything(tmp_path):
    code, _ = run(fleet_args(tmp_path, chain(tmp_path), "-y", "--only", "nowhere"),
                  update_one=lambda step: None)
    assert code == cli.BLOCKED


def test_the_device_this_pc_is_behind_goes_last(tmp_path):
    ran: list[str] = []
    run(fleet_args(tmp_path, chain(tmp_path), "-y", "--confirm-order",
                   "--here", "leaf"),
        update_one=lambda step: ran.append(step.name))
    assert ran == ["relay", "core", "leaf"]


def test_a_device_already_on_the_target_is_not_touched(tmp_path):
    path = tmp_path / "inv.json"
    save(path, Inventory(devices=[
        device("done", address="192.0.2.1", version="7.16.1"),
        device("behind", address="192.0.2.2")]))
    ran: list[str] = []
    code, out = run(fleet_args(tmp_path, path, "-y", "--confirm-order"),
                    update_one=lambda step: ran.append(step.name))
    assert code == cli.OK, out
    assert ran == ["behind"]
