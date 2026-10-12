"""`mqs scan` and `mqs inventory` from the outside.

The shape being checked: a scan writes the inventory and everything else reads it, so a
survey and an update are separate acts with a file in between rather than one long
command somebody has to get right first time.
"""

from __future__ import annotations

import io
import json

from factories import make_inventory, make_radios
from mqs import cli
from mqs.model.inventory import PackageInfo
from mqs.transport.mndp import DiscoveredDevice

MB = 1024 * 1024
MAC_A = "AA:BB:CC:00:00:01"


class FakeScanner:
    def __init__(self, devices: list[DiscoveredDevice]) -> None:
        self.found = devices

    def __enter__(self) -> FakeScanner:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def open(self) -> None:
        return None

    def solicit(self) -> None:
        return None

    def poll(self, timeout: float) -> list:
        return []

    def devices(self, max_age: float | None = None) -> list:
        return self.found


def announced(mac: str = MAC_A, identity: str = "sxt-01", address: str = "192.0.2.5"):
    from ipaddress import IPv4Address
    return DiscoveredDevice(mac=mac, identity=identity, version="7.15.3",
                            platform="MikroTik", board="SXTsq 5 nD",
                            ipv4=IPv4Address(address), local_interface="eth0")


def antenna_inventory(identity: str = "sxt-01", version: str = "7.15.3",
                      serial: str = "SN1", radios=None):
    inventory = make_inventory(
        version, architecture="arm", model="RBSXTsq5nD", free_hdd_space=256 * MB,
        factory_firmware="7.10.0",
        radios=radios if radios is not None else make_radios(
            ("wlan1", "wireless", "ap-bridge"), registrations={"wlan1": 2}),
        packages=[PackageInfo(name="routeros", version=version),
                  PackageInfo(name="wireless", version=version)])
    board = inventory.routerboard.model_copy(update={"serial_number": serial})
    return inventory.model_copy(update={"identity": identity, "routerboard": board})


def run(argv: list[str], **deps) -> tuple[int, str]:
    out = io.StringIO()
    return cli.main(argv, out=out, **deps), out.getvalue()


def scan_args(tmp_path, *extra: str) -> list[str]:
    return ["scan", "--seconds", "0", "--inventory", str(tmp_path / "inv.json"), *extra]


# ------------------------------------------------------------------ scanning


def test_a_scan_lists_what_it_found_and_how_sure_it_is(tmp_path, monkeypatch):
    monkeypatch.setattr("mqs.discover.cli.MndpScanner",
                        lambda interfaces: FakeScanner([announced()]))
    monkeypatch.setattr("mqs.discover.cli.list_ipv4_interfaces", list)
    code, out = run(scan_args(tmp_path))
    assert code == cli.OK, out
    assert "+  sxt-01" in out and "192.0.2.5" in out
    assert "mndp" in out


def test_a_scan_writes_the_inventory_and_says_where(tmp_path, monkeypatch):
    monkeypatch.setattr("mqs.discover.cli.MndpScanner",
                        lambda interfaces: FakeScanner([announced()]))
    monkeypatch.setattr("mqs.discover.cli.list_ipv4_interfaces", list)
    path = tmp_path / "inv.json"
    code, out = run(scan_args(tmp_path))
    assert code == cli.OK and path.is_file() and str(path) in out
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["devices"][0]["identity"] == "sxt-01"


def test_a_scan_can_be_told_not_to_write_anything(tmp_path, monkeypatch):
    monkeypatch.setattr("mqs.discover.cli.MndpScanner",
                        lambda interfaces: FakeScanner([announced()]))
    monkeypatch.setattr("mqs.discover.cli.list_ipv4_interfaces", list)
    code, _ = run(scan_args(tmp_path, "--no-save"))
    assert code == cli.OK and not (tmp_path / "inv.json").exists()


def test_a_scan_with_no_source_enabled_refuses(tmp_path):
    code, _ = run(scan_args(tmp_path, "--no-mndp"))
    assert code == cli.BLOCKED


def test_scanning_ports_without_a_subnet_refuses(tmp_path):
    """It would otherwise be a flag that silently does nothing."""
    code, _ = run(scan_args(tmp_path, "--no-mndp", "--scan-ports"))
    assert code == cli.BLOCKED


def test_logging_in_is_what_makes_a_device_updatable(tmp_path, monkeypatch):
    monkeypatch.setattr("mqs.discover.cli.MndpScanner",
                        lambda interfaces: FakeScanner([announced()]))
    monkeypatch.setattr("mqs.discover.cli.list_ipv4_interfaces", list)

    class Session:
        inventory = antenna_inventory()
        transport = None

    code, out = run(scan_args(tmp_path, "--login", "--user", "admin"),
                    connect=lambda *a, **k: Session())
    assert code == cli.OK, out
    assert "++ sxt-01" in out  # confirmed
    assert "SXT Square ap" in out
    saved = json.loads((tmp_path / "inv.json").read_text(encoding="utf-8"))
    assert saved["devices"][0]["serial_number"] == "SN1"


def test_a_login_also_records_what_the_device_sees(tmp_path, monkeypatch):
    """The AP's registration table names a station MNDP never heard: it joins the
    inventory, hanging off the AP, which is what the batch order is drawn from."""
    monkeypatch.setattr("mqs.discover.cli.MndpScanner",
                        lambda interfaces: FakeScanner([announced(identity="ap-1")]))
    monkeypatch.setattr("mqs.discover.cli.list_ipv4_interfaces", list)

    class Transport:
        def execute(self, path, **args):
            if path == "/interface/wireless/registration-table/print":
                return [{"mac-address": "AA:BB:CC:00:00:77", "interface": "wlan1",
                         "identity": "cpe-1"}]
            return []

    class Session:
        inventory = antenna_inventory("ap-1")
        transport = Transport()
        session_port = "ether1"  # the station is on wlan1: behind the AP

    code, out = run(scan_args(tmp_path, "--login", "--user", "admin"),
                    connect=lambda *a, **k: Session())
    assert code == cli.OK, out
    saved = json.loads((tmp_path / "inv.json").read_text(encoding="utf-8"))
    station = next(d for d in saved["devices"] if d["identity"] == "cpe-1")
    assert station["sightings"][0]["found_at"] == "ap-1"


def test_a_refused_login_is_reported_and_the_scan_still_finishes(tmp_path, monkeypatch):
    monkeypatch.setattr("mqs.discover.cli.MndpScanner",
                        lambda interfaces: FakeScanner([announced()]))
    monkeypatch.setattr("mqs.discover.cli.list_ipv4_interfaces", list)

    def refuse(*a, **k):
        raise PermissionError("invalid user name or password")

    code, out = run(scan_args(tmp_path, "--login", "--user", "admin"), connect=refuse)
    assert code == cli.OK, out
    assert "login refused" in out and "invalid user name" in out


# ------------------------------------------------------------------ the diff


def _scan_twice(tmp_path, monkeypatch, first, second, *extra: str):
    monkeypatch.setattr("mqs.discover.cli.list_ipv4_interfaces", list)
    monkeypatch.setattr("mqs.discover.cli.MndpScanner",
                        lambda interfaces: FakeScanner([announced()]))
    run(scan_args(tmp_path, "--login", "--user", "admin", *extra),
        connect=lambda *a, **k: type("S", (), {"inventory": first, "transport": None})())
    return run(scan_args(tmp_path, "--login", "--user", "admin", *extra),
               connect=lambda *a, **k: type("S", (), {"inventory": second,
                                                      "transport": None})())


def test_a_second_scan_says_what_moved(tmp_path, monkeypatch):
    code, out = _scan_twice(tmp_path, monkeypatch, antenna_inventory(),
                            antenna_inventory(version="7.16.1"))
    assert code == cli.OK, out
    assert "version" in out and "7.15.3 -> 7.16.1" in out


def test_an_unchanged_network_says_so(tmp_path, monkeypatch):
    code, out = _scan_twice(tmp_path, monkeypatch, antenna_inventory(),
                            antenna_inventory())
    assert code == cli.OK and "nothing changed since the last scan" in out


def test_a_radio_that_disappeared_fails_the_run(tmp_path, monkeypatch):
    """This gets run from a scheduled task, and something has to notice."""
    from mqs.model.inventory import WirelessSnapshot

    code, out = _scan_twice(tmp_path, monkeypatch, antenna_inventory(),
                            antenna_inventory(radios=WirelessSnapshot(collected=True)))
    assert code == cli.FAILED
    assert "!! radio_lost" in out


def test_a_version_that_went_backwards_fails_the_run_too(tmp_path, monkeypatch):
    code, out = _scan_twice(tmp_path, monkeypatch, antenna_inventory(version="7.16.1"),
                            antenna_inventory(version="7.15.3"))
    assert code == cli.FAILED and "!! downgrade" in out


# ------------------------------------------------------------------ inventory


def _written(tmp_path, monkeypatch, inventory=None):
    monkeypatch.setattr("mqs.discover.cli.list_ipv4_interfaces", list)
    monkeypatch.setattr("mqs.discover.cli.MndpScanner",
                        lambda interfaces: FakeScanner([announced()]))
    session = type("S", (), {"inventory": inventory or antenna_inventory(),
                             "transport": None})
    run(scan_args(tmp_path, "--login", "--user", "admin"),
        connect=lambda *a, **k: session())
    return tmp_path / "inv.json"


def test_the_inventory_can_be_read_back(tmp_path, monkeypatch):
    path = _written(tmp_path, monkeypatch)
    code, out = run(["inventory", "--inventory", str(path)])
    assert code == cli.OK and "sxt-01" in out and "1 device" in out


def test_the_inventory_exports_to_csv(tmp_path, monkeypatch):
    path = _written(tmp_path, monkeypatch)
    target = tmp_path / "out.csv"
    code, out = run(["inventory", "--inventory", str(path), "--export", str(target)])
    assert code == cli.OK and "1 device" in out
    text = target.read_text(encoding="utf-8")
    assert text.startswith("identity;address;mac;")
    assert "sxt-01" in text and "SXT Square" in text


def test_the_inventory_exports_to_json(tmp_path, monkeypatch):
    path = _written(tmp_path, monkeypatch)
    target = tmp_path / "out.json"
    run(["inventory", "--inventory", str(path), "--export", str(target),
         "--format", "json"])
    assert json.loads(target.read_text(encoding="utf-8"))["devices"][0]["identity"] == \
        "sxt-01"


def test_the_inventory_can_be_narrowed_to_what_matters(tmp_path, monkeypatch):
    from mqs.model.inventory import WirelessSnapshot

    path = _written(tmp_path, monkeypatch,
                    antenna_inventory(radios=WirelessSnapshot(collected=True)))
    _, all_rows = run(["inventory", "--inventory", str(path)])
    _, wireless = run(["inventory", "--inventory", str(path), "--wireless-only"])
    assert "sxt-01" in all_rows and "sxt-01" not in wireless


def test_a_missing_inventory_is_empty_and_not_an_error(tmp_path):
    code, out = run(["inventory", "--inventory", str(tmp_path / "nope.json")])
    assert code == cli.OK and "0 device" in out


def test_an_inventory_from_another_format_is_refused(tmp_path):
    path = tmp_path / "inv.json"
    path.write_text(json.dumps({"format": 99, "devices": []}), encoding="utf-8")
    code, _ = run(["inventory", "--inventory", str(path)])
    assert code == cli.FAILED


# ------------------------------------------------------------------ limiting the login


def _two_found(monkeypatch):
    monkeypatch.setattr("mqs.discover.cli.list_ipv4_interfaces", list)
    monkeypatch.setattr("mqs.discover.cli.MndpScanner", lambda interfaces: FakeScanner([
        announced(MAC_A, "mine", "192.0.2.5"),
        announced("AA:BB:CC:00:00:02", "somebody-elses", "192.0.2.6")]))


def test_login_only_touches_the_devices_it_names(tmp_path, monkeypatch):
    """A scan on a live network hears equipment that is not yours, and `--login` without
    this would try to log in to all of it."""
    _two_found(monkeypatch)
    asked: list[str] = []

    def connect(params, *a, **k):
        asked.append(params.host)
        return type("S", (), {"inventory": antenna_inventory(), "transport": None})()

    code, out = run(scan_args(tmp_path, "--login", "--user", "admin",
                              "--login-only", "mine"), connect=connect)
    assert code == cli.OK, out
    assert asked == ["192.0.2.5"]
    assert "logging in to 1" in out


def test_login_only_matches_an_address_too(tmp_path, monkeypatch):
    _two_found(monkeypatch)
    asked: list[str] = []
    run(scan_args(tmp_path, "--login", "--user", "admin",
                  "--login-only", "192.0.2.6"),
        connect=lambda params, *a, **k: asked.append(params.host) or type(
            "S", (), {"inventory": antenna_inventory(), "transport": None})())
    assert asked == ["192.0.2.6"]


def test_a_device_left_out_of_the_login_is_still_in_the_inventory(tmp_path, monkeypatch):
    _two_found(monkeypatch)
    run(scan_args(tmp_path, "--login", "--user", "admin", "--login-only", "mine"),
        connect=lambda *a, **k: type("S", (), {"inventory": antenna_inventory(),
                                               "transport": None})())
    saved = json.loads((tmp_path / "inv.json").read_text(encoding="utf-8"))
    assert {d["identity"] for d in saved["devices"]} == {"sxt-01", "somebody-elses"}


def test_login_only_that_matches_nothing_is_refused(tmp_path, monkeypatch):
    """Rather than quietly logging in to nothing and reporting a successful scan."""
    _two_found(monkeypatch)
    code, _ = run(scan_args(tmp_path, "--login", "--user", "admin",
                            "--login-only", "nowhere"),
                  connect=lambda *a, **k: None)
    assert code == cli.BLOCKED


def test_without_the_flag_everything_found_is_tried(tmp_path, monkeypatch):
    _two_found(monkeypatch)
    asked: list[str] = []
    run(scan_args(tmp_path, "--login", "--user", "admin"),
        connect=lambda params, *a, **k: asked.append(params.host) or type(
            "S", (), {"inventory": antenna_inventory(), "transport": None})())
    assert sorted(asked) == ["192.0.2.5", "192.0.2.6"]
