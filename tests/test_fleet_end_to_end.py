"""`mqs fleet` all the way down to the reboot, against simulated devices.

The other fleet tests inject `update_one` and check the ordering around it. This one does
not: it drives the real `updater` — open a session, build the context, run the version
change, read the radios back — so the part of the batch that actually reboots hardware is
exercised by something other than hardware.
"""

from __future__ import annotations

import io
import time
from pathlib import Path

import pytest

from factories import make_inventory, make_radios
from factories import make_npk as npk
from fake_router import FakeRouter, RouterUploader
from mqs import cli
from mqs.apply.session import DeviceSession
from mqs.apply.version_change import VersionChanger
from mqs.discover.candidates import Candidate, Sighting, Source, confirmed
from mqs.discover.store import Inventory, save
from mqs.model.inventory import PackageInfo
from mqs.transport.base import ConnectionParams

MB = 1024 * 1024


def arm_dir(tmp_path: Path, *versions: str) -> Path:
    folder = tmp_path / "Update"
    folder.mkdir(parents=True, exist_ok=True)
    for text in versions:
        parts = tuple(int(p) for p in [*text.split("."), "0", "0"][:3])
        (folder / f"routeros-{text}-arm.npk").write_bytes(npk("system", parts, "arm"))
        (folder / f"wireless-{text}-arm.npk").write_bytes(npk("wireless", parts, "arm"))
    return folder


def antenna(identity: str, *, address: str, mode: str = "station-bridge",
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
    return confirmed(Candidate(addresses=[address], sightings=sightings,
                               last_seen=time.time()), inventory)


class Lab:
    """A simulated device per address, each a FakeRouter that can be made to misbehave."""

    def __init__(self, tmp_path: Path, devices: dict[str, Candidate], target: str,
                 *, loses: tuple[str, ...] = ()) -> None:
        self.routers: dict[str, FakeRouter] = {}
        self.by_identity: dict[str, FakeRouter] = {}
        self.sessions: dict[str, DeviceSession] = {}
        self.folder = arm_dir(tmp_path, "7.15.3", target)
        for address, device in devices.items():
            router = FakeRouter()
            router.version = f"{device.version} (long-term)"
            router.firmware.update({"current-firmware": "7.10.0",
                                    "upgrade-firmware": "7.10.0"})
            router.packages = {"routeros": device.version, "wireless": device.version}
            mode = device.radios.interfaces[0].mode
            router.radios = {"wireless": [{"name": "wlan1", "mode": mode,
                                           "running": "yes"}]}
            router.installable = {f"routeros-{target}-arm.npk": target}
            router.driver_files = {f"wireless-{target}-arm.npk": "wireless"}
            if device.identity in loses:
                router.loses_drivers = {"wireless"}
            self.routers[address] = router
            self.by_identity[device.identity] = router
            self.sessions[address] = DeviceSession(
                ConnectionParams(host=address, username="admin"), router.session(),
                device.inventory, {}, "ether1")

    def connect(self, params, *a, **k) -> DeviceSession:
        return self.sessions[params.host]

    def changer(self, ctx):
        """The same offline wiring the single-device tests use: no sleeping, no network.

        Keyed by identity, not by the transport's host: a FakeRouter session carries its
        own address, which is not the one the batch connected to.
        """
        router = self.by_identity[ctx.inventory.identity]
        ctx.uploader_factory = lambda transport: RouterUploader(router)
        ctx.sleep = lambda seconds: None
        ctx.reconnect = lambda target: router.session(port=target.api_port)
        ctx.registration_timeout_s = 0.0
        return VersionChanger(ctx)


def inventory_file(tmp_path: Path, devices: list[Candidate]) -> Path:
    path = tmp_path / "inv.json"
    save(path, Inventory(saved_at=time.time(), devices=devices))
    return path


def run(argv: list[str], **deps) -> tuple[int, str]:
    out = io.StringIO()
    return cli.main(argv, out=out, **deps), out.getvalue()


def chain(tmp_path: Path, *, loses: tuple[str, ...] = (), target: str = "7.16.1"):
    devices = [antenna("core", address="192.0.2.1", mode="ap-bridge"),
               antenna("relay", address="192.0.2.2", mode="ap-bridge", parent="core"),
               antenna("leaf", address="192.0.2.3", parent="relay")]
    lab = Lab(tmp_path, {d.address: d for d in devices}, target, loses=loses)
    path = inventory_file(tmp_path, devices)
    argv = ["fleet", "--to", target, "--inventory", str(path),
            "--update-dir", str(lab.folder), "-y"]
    return lab, argv


# ------------------------------------------------------------------ the whole way down


def test_a_batch_really_updates_every_device_leaves_first(tmp_path):
    lab, argv = chain(tmp_path)
    code, out = run(argv, connect=lab.connect, changer_factory=lab.changer)
    assert code == cli.OK, out
    assert "3 device(s) done" in out
    # Every device rebooted once, onto the new version, with its driver carried.
    for address, router in lab.routers.items():
        assert router.reboots == 1, address
        assert router.packages == {"routeros": "7.16.1", "wireless": "7.16.1"}, address
        assert router.radios["wireless"][0]["name"] == "wlan1"
    # Both files went up on each device, and nothing was left behind.
    for router in lab.routers.values():
        assert not [name for name in router.files if name.endswith(".npk")]


@pytest.mark.parametrize("flag, wait", [([], True), (["--no-wait-stations"], False)])
def test_waiting_for_stations_reaches_every_device(tmp_path, flag, wait):
    lab, argv = chain(tmp_path)
    seen: list[bool] = []

    def changer(ctx):
        seen.append(ctx.wait_for_stations)
        return lab.changer(ctx)

    code, out = run([*argv, *flag], connect=lab.connect, changer_factory=changer)
    assert code == cli.OK, out
    assert seen == [wait] * 3


def test_the_order_holds_all_the_way_through_a_real_run(tmp_path):
    lab, argv = chain(tmp_path)
    code, out = run(argv, connect=lab.connect, changer_factory=lab.changer)
    assert code == cli.OK
    # The reboots happened in batch order, which is what the log shows the engineer.
    order = [line.split("OK ")[1].strip() for line in out.splitlines()
             if line.strip().startswith("OK ")]
    assert order == ["leaf", "relay", "core"]


def test_a_device_that_comes_back_without_its_radio_stops_the_batch(tmp_path):
    """The whole point, end to end: the relay loses its driver, and `core` — which
    everything behind it is reached through — is never touched."""
    lab, argv = chain(tmp_path, loses=("relay",))
    code, out = run(argv, connect=lab.connect, changer_factory=lab.changer)
    assert code == cli.FAILED
    assert "Dávka se zastavila na zařízení relay" in out
    assert lab.routers["192.0.2.3"].reboots == 1  # leaf went first and was fine
    assert lab.routers["192.0.2.2"].reboots == 1  # relay rebooted and came back broken
    assert lab.routers["192.0.2.1"].reboots == 0  # core was never touched
    assert "1 zařízení se neaktualizovalo" in out


def test_the_failure_names_the_radio_that_went_missing(tmp_path):
    lab, argv = chain(tmp_path, loses=("leaf",))
    _code, out = run(argv, connect=lab.connect, changer_factory=lab.changer)
    assert "wlan1" in out


def test_a_backup_is_taken_on_every_device_before_its_packages(tmp_path):
    lab, argv = chain(tmp_path)
    run(argv, connect=lab.connect, changer_factory=lab.changer)
    for address, router in lab.routers.items():
        assert "/system/backup/save" in router.log, address
        assert "/export" in router.log, address
        assert router.log.index("/system/backup/save") < router.log.index("/system/reboot")


def test_a_device_that_moved_since_it_was_read_stops_the_batch_untouched(tmp_path):
    """The inventory says 7.15.3; the leaf runs 7.16.1 by now. Its plan is not run."""
    lab, argv = chain(tmp_path)
    session = lab.sessions["192.0.2.3"]
    session.inventory = make_inventory(
        "7.16.1", architecture="arm", model="RBSXTsq5nD", free_hdd_space=256 * MB,
        factory_firmware="7.10.0", radios=make_radios(("wlan1", "wireless", "station-bridge")),
        packages=[PackageInfo(name="routeros", version="7.16.1"),
                  PackageInfo(name="wireless", version="7.16.1")])
    code, out = run(argv, connect=lab.connect, changer_factory=lab.changer)
    assert code == cli.FAILED
    assert "je jinde, než říká inventář" in out
    assert all(router.reboots == 0 for router in lab.routers.values())


def test_a_dry_run_reboots_nothing(tmp_path):
    lab, argv = chain(tmp_path)
    code, out = run([*argv, "--dry-run"], connect=lab.connect,
                    changer_factory=lab.changer)
    assert code == cli.OK
    assert all(router.reboots == 0 for router in lab.routers.values())
    assert all(not router.log for router in lab.routers.values())
    assert "dry run" in out


def test_only_updates_only_that_device(tmp_path):
    lab, argv = chain(tmp_path)
    code, _ = run([*argv, "--only", "leaf"], connect=lab.connect,
                  changer_factory=lab.changer)
    assert code == cli.OK
    assert lab.routers["192.0.2.3"].reboots == 1
    assert lab.routers["192.0.2.1"].reboots == 0
    assert lab.routers["192.0.2.2"].reboots == 0
