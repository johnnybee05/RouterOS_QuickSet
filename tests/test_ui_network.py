"""The *Síť* page.

The page owns no logic, so these tests check the two things it is actually for: that the
table makes it obvious which devices may be updated, and that the order it shows is the
order the batch would use.
"""

from __future__ import annotations

import json
import time

import pytest

from factories import make_inventory, make_radios
from mqs.discover.candidates import Candidate, Sighting, Source, confirmed, from_sighting
from mqs.discover.scan import ScanResult, SourceResult
from mqs.discover.store import Inventory, save
from mqs.model.inventory import PackageInfo

pytest.importorskip("PySide6")
pytestmark = pytest.mark.qt

MB = 1024 * 1024


def antenna(identity: str, *, address: str, mode: str = "station-bridge",
            parent: str = "", version: str = "7.15.3", serial: str = "") -> Candidate:
    inventory = make_inventory(
        version, architecture="arm", model="RBSXTsq5nD", board_name="SXTsq 5 nD",
        free_hdd_space=256 * MB, factory_firmware="7.10.0",
        radios=make_radios(("wlan1", "wireless", mode)),
        packages=[PackageInfo(name="routeros", version=version),
                  PackageInfo(name="wireless", version=version)])
    board = inventory.routerboard.model_copy(
        update={"serial_number": serial or identity.upper()})
    inventory = inventory.model_copy(update={"identity": identity, "routerboard": board})
    sightings = [Sighting(source=Source.MNDP, address=address, identity=identity)]
    if parent:
        sightings.append(Sighting(source=Source.REGISTRATION, found_at=parent,
                                  via_interface="wlan1"))
    base = Candidate(addresses=[address], sightings=sightings, last_seen=time.time())
    return confirmed(base, inventory)


def guess(identity: str, address: str = "192.0.2.9") -> Candidate:
    return from_sighting(Sighting(source=Source.MNDP, identity=identity, address=address,
                                  macs=["AA:BB:CC:00:00:99"]))


@pytest.fixture
def page(qtbot, tmp_path):
    from mqs.ui.controller import ProfileController
    from mqs.ui.dialogs import Dialogs
    from mqs.ui.pages.network import NetworkPage
    from mqs.ui.workers import TaskRunner

    widget = NetworkPage(ProfileController(), TaskRunner(), Dialogs(),
                         inventory_path=tmp_path / "inv.json",
                         scanner_factory=lambda: None,
                         scan_fn=lambda *a, **k: ScanResult())
    qtbot.addWidget(widget)
    return widget


def texts(page, column: int) -> list[str]:
    return [page.table.item(row, column).text() for row in range(page.table.rowCount())]


# ------------------------------------------------------------------ the table


def test_the_state_column_says_what_may_be_updated(page):
    page.devices = [antenna("sxt-01", address="192.0.2.5"), guess("maybe")]
    page._show()
    assert texts(page, 0) == ["++", "+"]
    assert [d.identity for d in page.updatable] == ["sxt-01"]


def test_a_device_fills_every_column_it_can(page):
    page.devices = [antenna("sxt-01", address="192.0.2.5")]
    page._show()
    row = [page.table.item(0, c).text() for c in range(page.table.columnCount())]
    assert row[1] == "sxt-01" and row[2] == "192.0.2.5"
    assert row[3] == "RBSXTsq5nD" and row[4] == "SXT Square"
    assert row[5] == "station" and row[6] == "7.15.3"
    assert row[7] == "arm" and row[8] == "wireless"
    assert "mndp" in row[9] and "login" in row[9]


def test_a_device_nobody_logged_into_leaves_the_device_columns_empty(page):
    page.devices = [guess("maybe")]
    page._show()
    row = [page.table.item(0, c).text() for c in range(page.table.columnCount())]
    assert row[0] == "+" and row[1] == "maybe"
    assert row[5] == "" and row[6] == ""  # no role, and no version read off a device


def test_a_refused_login_is_in_the_tooltip_rather_than_lost(page):
    device = guess("locked").model_copy(update={"login_error": "wrong password"})
    page.devices = [device]
    page._show()
    assert page.table.item(0, 0).toolTip() == "wrong password"


def test_a_conflict_is_shown_on_the_row(page):
    device = antenna("sxt-01", address="192.0.2.5").model_copy(
        update={"conflicts": ["192.0.2.5: also lhg-02"]})
    page.devices = [device]
    page._show()
    assert "also lhg-02" in page.table.item(0, 1).toolTip()


# ------------------------------------------------------------------ the order


def test_the_order_runs_from_the_leaves_inward(page):
    page.devices = [antenna("core", address="192.0.2.1", mode="ap-bridge"),
                    antenna("relay", address="192.0.2.2", mode="ap-bridge",
                            parent="core"),
                    antenna("leaf", address="192.0.2.3", parent="relay")]
    page._show()
    text = page.order_text.toPlainText()
    assert text.index("leaf") < text.index("relay") < text.index("core")
    assert "1. leaf" in text and "3. core" in text


def test_the_tree_is_shown_under_the_order(page):
    page.devices = [antenna("core", address="192.0.2.1", mode="ap-bridge"),
                    antenna("leaf", address="192.0.2.2", parent="core")]
    page._show()
    assert "core (wlan1) -> leaf" in page.order_text.toPlainText()


def test_a_device_that_cannot_be_updated_is_listed_as_such(page):
    page.devices = [antenna("core", address="192.0.2.1", mode="ap-bridge"),
                    guess("maybe")]
    page._show()
    text = page.order_text.toPlainText()
    assert "maybe" in text and "nepotvrzeno" in text


def test_an_incomplete_topology_says_the_order_is_only_a_suggestion(page):
    page.devices = [antenna("a", address="192.0.2.1"), antenna("b", address="192.0.2.2")]
    page._show()
    assert "jen návrh" in page.order_hint.text()


def test_a_complete_topology_says_how_many_it_ordered(page):
    page.devices = [antenna("core", address="192.0.2.1", mode="ap-bridge"),
                    antenna("leaf", address="192.0.2.2", parent="core")]
    page._show()
    assert "2 zařízení" in page.order_hint.text()


# ------------------------------------------------------------------ scanning


def test_a_scan_merges_into_the_inventory_and_saves_it(page, qtbot, tmp_path):
    found = ScanResult((antenna("sxt-01", address="192.0.2.5"),),
                       (SourceResult(Source.MNDP, (), "", 1.0),))
    page.scan_fn = lambda *a, **k: found
    with qtbot.waitSignal(page.devicesChanged, timeout=3000):
        page.start_scan()
    assert [d.identity for d in page.devices] == ["sxt-01"]
    assert "Nalezeno 1" in page.status.text()
    saved = json.loads((tmp_path / "inv.json").read_text(encoding="utf-8"))
    assert saved["devices"][0]["identity"] == "sxt-01"


def test_a_scan_reporting_its_sources_still_lands(page, qtbot):
    # The real scan calls on_source(source, state); the worker's progress takes one value.
    def scan(plan, **k):
        k["on_source"](Source.MNDP, "running")
        k["on_source"](Source.MNDP, "ok")
        return ScanResult((antenna("sxt-01", address="192.0.2.5"),), ())
    page.scan_fn = scan
    with qtbot.waitSignal(page.devicesChanged, timeout=3000):
        page.start_scan()
    assert [d.identity for d in page.devices] == ["sxt-01"]


def test_a_failed_source_is_reported_and_the_scan_still_lands(page, qtbot):
    found = ScanResult((antenna("sxt-01", address="192.0.2.5"),),
                       (SourceResult(Source.SCAN, (), "subnet too large", 0.1),))
    page.scan_fn = lambda *a, **k: found
    with qtbot.waitSignal(page.devicesChanged, timeout=3000):
        page.start_scan()
    assert "subnet too large" in page.status.text()
    assert page.devices


def test_a_second_scan_marks_what_changed_in_bold(page, qtbot):
    page.scan_fn = lambda *a, **k: ScanResult(
        (antenna("sxt-01", address="192.0.2.5"),), ())
    with qtbot.waitSignal(page.devicesChanged, timeout=3000):
        page.start_scan()
    page.scan_fn = lambda *a, **k: ScanResult(
        (antenna("sxt-01", address="192.0.2.5", version="7.16.1"),), ())
    with qtbot.waitSignal(page.devicesChanged, timeout=3000):
        page.start_scan()
    assert page.table.item(0, 6).font().bold()
    assert "7.15.3 -> 7.16.1" in page.table.item(0, 6).toolTip()


def test_a_radio_that_disappeared_is_shouted_about(page, qtbot):
    from mqs.model.inventory import WirelessSnapshot

    page.scan_fn = lambda *a, **k: ScanResult(
        (antenna("sxt-01", address="192.0.2.5"),), ())
    with qtbot.waitSignal(page.devicesChanged, timeout=3000):
        page.start_scan()

    gone = antenna("sxt-01", address="192.0.2.5")
    gone = gone.model_copy(update={
        "inventory": gone.inventory.model_copy(
            update={"radios": WirelessSnapshot(collected=True)})})
    page.scan_fn = lambda *a, **k: ScanResult((gone,), ())
    with qtbot.waitSignal(page.devicesChanged, timeout=3000):
        page.start_scan()
    assert "POZOR" in page.order_hint.text()
    assert "sxt-01" in page.order_hint.text()


def test_scanning_ports_without_a_subnet_is_refused_before_anything_runs(page, monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(page.dialogs, "error",
                        lambda parent, text: shown.append(text))
    page.scan_ports.setChecked(True)
    started: list[int] = []
    page.scan_fn = lambda *a, **k: started.append(1) or ScanResult()
    page.start_scan()
    assert started == [] and shown and "Sítě" in shown[0]


def test_the_buttons_follow_what_is_happening(page):
    assert page.scan_button.isEnabled() and not page.stop_button.isEnabled()
    assert not page.export_button.isEnabled()  # nothing to export yet
    page.devices = [antenna("sxt-01", address="192.0.2.5")]
    page._update_buttons()
    assert page.export_button.isEnabled()


# ------------------------------------------------------------------ the inventory file


def test_a_saved_inventory_is_loaded_on_open(qtbot, tmp_path):
    from mqs.ui.controller import ProfileController
    from mqs.ui.dialogs import Dialogs
    from mqs.ui.pages.network import NetworkPage
    from mqs.ui.workers import TaskRunner

    path = tmp_path / "inv.json"
    save(path, Inventory(saved_at=time.time(),
                         devices=[antenna("sxt-01", address="192.0.2.5")]))
    widget = NetworkPage(ProfileController(), TaskRunner(), Dialogs(),
                         inventory_path=path, scanner_factory=lambda: None,
                         scan_fn=lambda *a, **k: ScanResult())
    qtbot.addWidget(widget)
    assert [d.identity for d in widget.devices] == ["sxt-01"]
    assert widget.table.rowCount() == 1


def test_an_unreadable_inventory_is_reported_and_the_page_still_opens(qtbot, tmp_path):
    from mqs.ui.controller import ProfileController
    from mqs.ui.dialogs import Dialogs
    from mqs.ui.pages.network import NetworkPage
    from mqs.ui.workers import TaskRunner

    path = tmp_path / "inv.json"
    path.write_text(json.dumps({"format": 99, "devices": []}), encoding="utf-8")
    widget = NetworkPage(ProfileController(), TaskRunner(), Dialogs(),
                         inventory_path=path, scanner_factory=lambda: None,
                         scan_fn=lambda *a, **k: ScanResult())
    qtbot.addWidget(widget)
    assert widget.devices == [] and "nedá přečíst" in widget.status.text()


def test_the_table_exports_to_csv(page, tmp_path, monkeypatch):
    page.devices = [antenna("sxt-01", address="192.0.2.5")]
    page._show()
    target = tmp_path / "out.csv"
    monkeypatch.setattr(page.dialogs, "save_file",
                        lambda *a, **k: target)
    page.export()
    text = target.read_text(encoding="utf-8")
    assert text.startswith("identity;address;mac;") and "sxt-01" in text
    assert "Zapsáno 1" in page.status.text()


def test_an_empty_page_says_what_to_do_rather_than_counting_nothing(page):
    page._show()
    assert "Prohledat síť" in page.order_hint.text()
    assert page.table.rowCount() == 0
