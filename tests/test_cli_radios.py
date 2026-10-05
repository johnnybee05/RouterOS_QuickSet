"""`mqs version` on a device with radios: what it prints, and what it refuses to do.

The CLI is the surface that gets pointed at real hardware, so what matters here is that an
engineer reading the output can see the radio the application found and the packages it is
about to upload, *before* saying yes.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from factories import make_inventory, make_radios
from factories import make_npk as npk
from fake_router import FakeRouter, RouterUploader
from mqs import cli
from mqs.apply.session import DeviceSession
from mqs.model.inventory import PackageInfo
from mqs.model.version import RosVersion
from mqs.transport.base import ConnectionParams

V = RosVersion.parse
MB = 1024 * 1024


@pytest.fixture
def router() -> FakeRouter:
    device = FakeRouter()
    device.version = "7.15.3 (long-term)"
    device.packages = {"routeros": "7.15.3", "wireless": "7.15.3"}
    device.radios = {"wireless": [{"name": "wlan1", "mode": "ap-bridge", "running": "yes"}]}
    device.registrations = {"wireless": [{"interface": "wlan1"}] * 2}
    return device


def arm_dir(tmp_path: Path, *entries: tuple[str, str]) -> Path:
    folder = tmp_path / "Update"
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in entries:
        version = V(text)
        parts = (version.major, version.minor, version.patch)
        header = "system" if name == "routeros" else name
        (folder / f"{name}-{text}-arm.npk").write_bytes(npk(header, parts, "arm"))
    return folder


def antenna(version: str = "7.15.3", *, stations: int = 2, extras: tuple[str, ...] = (),
            radios=None):
    return make_inventory(
        version, architecture="arm", free_hdd_space=256 * MB, model="RBSXTsq5nD",
        factory_firmware="7.10.0",
        radios=radios if radios is not None else make_radios(
            ("wlan1", "wireless", "ap-bridge"), registrations={"wlan1": stations}),
        packages=[PackageInfo(name="routeros", version=version),
                  PackageInfo(name="wireless", version=version),
                  *(PackageInfo(name=e, version=version) for e in extras)])


def offline_changer(router: FakeRouter):
    def factory(ctx):
        ctx.uploader_factory = lambda transport: RouterUploader(router)
        ctx.sleep = lambda seconds: None
        ctx.reconnect = lambda target: router.session(port=target.api_port)
        ctx.registration_timeout_s = 0.0
        from mqs.apply.version_change import VersionChanger
        return VersionChanger(ctx)
    return factory


def run(argv: list[str], router: FakeRouter, inventory) -> tuple[int, str]:
    out = io.StringIO()
    session = DeviceSession(ConnectionParams(host="10.0.0.1", username="admin"),
                            router.session(), inventory, {}, "ether9")
    return cli.main(argv, out=out, connect=lambda *a, **k: session,
                    changer_factory=offline_changer(router)), out.getvalue()


# ------------------------------------------------------------------ what it prints


def test_the_radios_are_printed_before_anything_about_an_update(router, tmp_path):
    folder = arm_dir(tmp_path, ("routeros", "7.15.3"), ("wireless", "7.15.3"))
    code, out = run(["version", "--host", "10.0.0.1", "--update-dir", str(folder)],
                    router, antenna())
    assert code == cli.OK, out
    assert "radios          : 1  driver: wireless" in out
    assert "wlan1" in out and "ap-bridge" in out and "2 station(s)" in out
    assert out.index("radios") < out.index("versions in")


def test_a_device_with_no_radio_says_so(router, tmp_path):
    folder = arm_dir(tmp_path, ("routeros", "7.15.3"))
    plain = make_inventory("7.15.3", architecture="arm", free_hdd_space=256 * MB)
    code, out = run(["version", "--host", "10.0.0.1", "--update-dir", str(folder)],
                    router, plain)
    assert code == cli.OK and "radios          : none" in out


def test_the_route_and_the_driver_are_printed_before_the_question(router, tmp_path):
    folder = arm_dir(tmp_path, ("routeros", "7.15.3"), ("wireless", "7.15.3"),
                     ("routeros", "7.16.1"), ("wireless", "7.16.1"))
    code, out = run(["version", "--host", "10.0.0.1", "--update-dir", str(folder),
                     "--to", "7.16.1", "--dry-run"], router, antenna())
    assert code == cli.OK, out
    assert "1. 7.16.1 (reboot): routeros-7.16.1-arm.npk, wireless-7.16.1-arm.npk" in out
    assert "wireless driver carried: wireless" in out


def test_a_three_stop_route_is_printed_stop_by_stop(router, tmp_path):
    folder = arm_dir(tmp_path, ("routeros", "6.49.22"), ("routeros", "7.12.4"),
                     ("wireless", "7.12.4"), ("routeros", "7.16.1"), ("wireless", "7.16.1"))
    v6 = make_inventory("6.48.6", architecture="arm", free_hdd_space=256 * MB,
                        factory_firmware="6.42.4", model="RBSXTsq5nD",
                        radios=make_radios(("wlan1", "wireless", "ap-bridge")),
                        packages=[PackageInfo(name="routeros-arm", version="6.48.6"),
                                  PackageInfo(name="wireless", version="6.48.6",
                                              bundle="routeros-arm")])
    code, out = run(["version", "--host", "10.0.0.1", "--update-dir", str(folder),
                     "--to", "7.16.1", "--dry-run"], router, v6)
    assert code == cli.OK, out
    stops = [line.strip() for line in out.splitlines()
             if line.strip()[:2] in ("1.", "2.", "3.")]
    assert stops == [
        # The v6 stop needs no driver file: the bundle carries it. The two v7 ones do.
        "1. 6.49.22 (reboot): routeros-6.49.22-arm.npk",
        "2. 7.12.4 (reboot): routeros-7.12.4-arm.npk, wireless-7.12.4-arm.npk",
        "3. 7.16.1 (reboot): routeros-7.16.1-arm.npk, wireless-7.16.1-arm.npk",
    ]


# ------------------------------------------------------------------ dry run


def test_a_dry_run_changes_nothing(router, tmp_path):
    folder = arm_dir(tmp_path, ("routeros", "7.16.1"), ("wireless", "7.16.1"))
    code, out = run(["version", "--host", "10.0.0.1", "--update-dir", str(folder),
                     "--to", "7.16.1", "--dry-run"], router, antenna())
    assert code == cli.OK
    assert "dry run: nothing was changed on the device" in out
    assert not router.files and router.reboots == 0
    # Not even the question was asked, let alone the backup.
    assert "/system/backup/save" not in router.log


def test_a_dry_run_of_a_blocked_route_still_says_why(router, tmp_path):
    """No `wireless` for the target: the honest answer, without touching the device."""
    folder = arm_dir(tmp_path, ("routeros", "7.16.1"))
    code, out = run(["version", "--host", "10.0.0.1", "--update-dir", str(folder),
                     "--to", "7.16.1", "--dry-run"], router, antenna())
    assert code == cli.BLOCKED
    assert "wireless" in out and router.reboots == 0


# ------------------------------------------------------------------ the two answers


def test_an_unanswered_driver_split_blocks_and_names_the_option(router, tmp_path):
    folder = arm_dir(tmp_path, ("routeros", "7.16.1"), ("wifi-qcom", "7.16.1"),
                     ("wifi-qcom-ac", "7.16.1"))
    device = make_inventory("7.12.4", architecture="arm", free_hdd_space=256 * MB,
                            model="RBD53iG-5HacD2HnD-XX", factory_firmware="7.10.0",
                            radios=make_radios(("wifi1", "wifi", "ap")),
                            packages=[PackageInfo(name="routeros", version="7.12.4"),
                                      PackageInfo(name="wifiwave2", version="7.12.4")])
    code, out = run(["version", "--host", "10.0.0.1", "--update-dir", str(folder),
                     "--to", "7.16.1", "--dry-run"], router, device)
    assert code == cli.BLOCKED
    assert "wifi-qcom" in out and "--wifi-package" in out

    answered, out2 = run(["version", "--host", "10.0.0.1", "--update-dir", str(folder),
                          "--to", "7.16.1", "--dry-run",
                          "--wifi-package", "wifi-qcom-ac"], router, device)
    assert answered == cli.OK, out2
    assert "wifi-qcom-ac-7.16.1-arm.npk" in out2 and "wifi-qcom-7.16.1-arm.npk" not in out2


def test_a_missing_ordinary_package_blocks_until_it_is_waived(router, tmp_path):
    folder = arm_dir(tmp_path, ("routeros", "7.16.1"), ("wireless", "7.16.1"))
    device = antenna(extras=("gps",))
    code, out = run(["version", "--host", "10.0.0.1", "--update-dir", str(folder),
                     "--to", "7.16.1", "--dry-run"], router, device)
    assert code == cli.BLOCKED and "--allow-missing-package gps" in out

    waived, out2 = run(["version", "--host", "10.0.0.1", "--update-dir", str(folder),
                        "--to", "7.16.1", "--dry-run",
                        "--allow-missing-package", "gps"], router, device)
    assert waived == cli.OK, out2


def test_a_driver_can_never_be_waived(router, tmp_path):
    folder = arm_dir(tmp_path, ("routeros", "7.16.1"))
    code, out = run(["version", "--host", "10.0.0.1", "--update-dir", str(folder),
                     "--to", "7.16.1", "--dry-run",
                     "--allow-missing-package", "wireless"], router, antenna())
    assert code == cli.BLOCKED
    assert "bezdrátové rozhraní" in out  # the driver message, not the waivable one


def test_a_driver_switch_is_refused_from_the_command_line(router, tmp_path):
    folder = arm_dir(tmp_path, ("routeros", "7.16.1"), ("wifi-qcom-ac", "7.16.1"))
    code, out = run(["version", "--host", "10.0.0.1", "--update-dir", str(folder),
                     "--to", "7.16.1", "--dry-run",
                     "--wifi-package", "wifi-qcom-ac"], router, antenna())
    assert code == cli.BLOCKED and "Změna ovladače" in out


# ------------------------------------------------------------------ nothing read, nothing done


def test_without_a_radio_snapshot_nothing_is_planned(router, tmp_path):
    """The whole point of the refusal: an unread snapshot is not a router."""
    from mqs.model.inventory import WirelessSnapshot

    folder = arm_dir(tmp_path, ("routeros", "7.16.1"), ("wireless", "7.16.1"))
    code, out = run(["version", "--host", "10.0.0.1", "--update-dir", str(folder),
                     "--to", "7.16.1", "--dry-run"], router,
                    antenna(radios=WirelessSnapshot()))
    assert code == cli.BLOCKED
    assert "radios          : not read" in out
    assert "nikdo se nedíval" in out
