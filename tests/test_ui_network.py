"""The *Hromadný update* page.

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
                         scan_fn=lambda *a, **k: ScanResult(),
                         update_dir=tmp_path / "Update")
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
    assert row[5] == "station" and row[6] == "7.15.3" and row[7] == "7.10"
    assert row[8] == "arm" and row[9] == "wireless"
    assert "mndp" in row[10] and "login" in row[10]


def test_a_device_nobody_logged_into_leaves_the_device_columns_empty(page):
    page.devices = [guess("maybe")]
    page._show()
    row = [page.table.item(0, c).text() for c in range(page.table.columnCount())]
    assert row[0] == "+" and row[1] == "maybe"
    assert row[5] == "" and row[6] == ""  # no role, and no version read off a device
    assert row[7] == ""  # nor the oldest version the board takes


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
    assert "core (wlan1) → leaf" in page.order_text.toPlainText()


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


def test_a_new_scan_drops_what_it_did_not_hear(page, qtbot, tmp_path):
    old = from_sighting(Sighting(source=Source.MNDP, identity="old", address="192.0.2.8",
                                 macs=["AA:BB:CC:00:00:08"]))
    page.scan_fn = lambda *a, **k: ScanResult((old, guess("stays", "192.0.2.9")), ())
    with qtbot.waitSignal(page.devicesChanged, timeout=3000):
        page.start_scan()
    assert sorted(d.identity for d in page.devices) == ["old", "stays"]

    page.scan_fn = lambda *a, **k: ScanResult((guess("stays", "192.0.2.9"),), ())
    with qtbot.waitSignal(page.devicesChanged, timeout=3000):
        page.start_scan()
    assert [d.identity for d in page.devices] == ["stays"]
    assert texts(page, 1) == ["stays"]
    assert "Nalezeno 1" in page.status.text() and "old" in page.status.text()
    saved = json.loads((tmp_path / "inv.json").read_text(encoding="utf-8"))
    assert [d["identity"] for d in saved["devices"]] == ["stays"]


def test_a_device_heard_again_keeps_what_the_login_read(page, qtbot):
    logged_in = antenna("sxt-01", address="192.0.2.5").model_copy(
        update={"macs": ["AA:BB:CC:00:00:05"]})
    page.devices = [logged_in]
    page._show()
    heard = from_sighting(Sighting(source=Source.MNDP, identity="sxt-01",
                                   address="192.0.2.5", macs=["AA:BB:CC:00:00:05"]))
    page.scan_fn = lambda *a, **k: ScanResult((heard,), ())
    with qtbot.waitSignal(page.devicesChanged, timeout=3000):
        page.start_scan()
    assert len(page.devices) == 1
    assert page.devices[0].inventory is not None and texts(page, 0) == ["++"]


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


def test_every_start_begins_with_an_empty_list(qtbot, tmp_path):
    """What the last session saved is for `mqs fleet`; the window starts from nothing."""
    from mqs.ui.controller import ProfileController
    from mqs.ui.dialogs import Dialogs
    from mqs.ui.pages.network import NetworkPage
    from mqs.ui.workers import TaskRunner

    path = tmp_path / "inv.json"
    save(path, Inventory(saved_at=time.time(),
                         devices=[antenna("sxt-01", address="192.0.2.5")]))
    widget = NetworkPage(ProfileController(), TaskRunner(), Dialogs(),
                         inventory_path=path, scanner_factory=lambda: None,
                         scan_fn=lambda *a, **k: ScanResult(),
                         update_dir=tmp_path / "Update")
    qtbot.addWidget(widget)
    assert widget.devices == [] and widget.table.rowCount() == 0
    assert "Prohledat síť" in widget.order_hint.text()


def test_an_unreadable_inventory_file_does_not_stop_the_page_opening(qtbot, tmp_path):
    from mqs.ui.controller import ProfileController
    from mqs.ui.dialogs import Dialogs
    from mqs.ui.pages.network import NetworkPage
    from mqs.ui.workers import TaskRunner

    path = tmp_path / "inv.json"
    path.write_text(json.dumps({"format": 99, "devices": []}), encoding="utf-8")
    widget = NetworkPage(ProfileController(), TaskRunner(), Dialogs(),
                         inventory_path=path, scanner_factory=lambda: None,
                         scan_fn=lambda *a, **k: ScanResult(),
                         update_dir=tmp_path / "Update")
    qtbot.addWidget(widget)
    assert widget.devices == [] and widget.status.text() == ""


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


# ------------------------------------------------------------------ logging in


def settings(user: str = "bee"):
    from mqs.apply.session import LoginSettings
    from mqs.transport.base import ConnectionParams

    return LoginSettings(ConnectionParams(host="", username=user, key_filename="id_rsa"),
                         api_port=8729, ssh_port=122)


class Opened:
    """What open_fn hands back: a session with an inventory and a transport."""

    def __init__(self, inventory) -> None:
        self.inventory = inventory
        self.transport = object()
        self.session_port = "ether1"


def select(page, *rows: int) -> None:
    from PySide6.QtCore import QItemSelectionModel

    page.table.clearSelection()
    for row in rows:
        page.table.selectionModel().select(
            page.table.model().index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)


def test_logging_in_needs_a_selection(page):
    page.devices = [guess("maybe")]
    page._show()
    assert not page.login_button.isEnabled()
    select(page, 0)
    assert page.login_button.isEnabled()


def test_logging_in_without_a_user_on_the_device_page_says_so(page, monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(page.dialogs, "error", lambda parent, text: shown.append(text))
    page.devices = [guess("maybe")]
    page._show()
    select(page, 0)
    page.start_login()
    assert shown and "Zařízení" in shown[0]


def test_a_login_confirms_the_device_and_records_what_it_sees(page, qtbot, tmp_path):
    """The AP's registration table names a station: it joins the list hanging off it."""
    found = antenna("ap-1", address="192.0.2.9", mode="ap-bridge").inventory
    opened: list[str] = []
    closed: list[object] = []
    page.login_settings = settings
    page.open_fn = lambda s, host, cancelled: opened.append(host) or Opened(found)
    page.close_fn = closed.append
    asked: dict = {}

    def neighbours(transport, version, *, found_at, session_port, own_addresses):
        asked.update(session_port=session_port, own=own_addresses)
        return [Sighting(source=Source.REGISTRATION, found_at=found_at, identity="cpe-1",
                         macs=["AA:BB:CC:00:00:42"], via_interface="wlan1")]

    page.neighbours_fn = neighbours
    page.local_addresses_fn = lambda: ["192.168.55.55"]
    page.devices = [guess("ap-1")]
    page._show()
    select(page, 0)
    with qtbot.waitSignal(page.devicesChanged, timeout=3000):
        page.start_login()
    assert opened == ["192.0.2.9"] and len(closed) == 1
    assert asked == {"session_port": "ether1", "own": ["192.168.55.55"]}
    assert texts(page, 0) == ["++", "+"]
    assert texts(page, 1) == ["ap-1", "cpe-1"]
    # Still only the AP selected, and the tree still shows what hangs off it.
    assert "ap-1 (wlan1) → cpe-1" in page.order_text.toPlainText()
    assert "Načteno 1" in page.status.text()
    saved = json.loads((tmp_path / "inv.json").read_text(encoding="utf-8"))
    assert saved["devices"][0]["inventory"] is not None


def test_a_refused_login_is_counted_and_logged(page, qtbot):
    from mqs.transport.base import AuthError

    page.login_settings = settings

    def refuse(s, host, cancelled):
        raise AuthError("invalid user name or password")

    page.open_fn = refuse
    page.devices = [guess("locked")]
    page._show()
    select(page, 0)
    with qtbot.waitSignal(page.devicesChanged, timeout=3000):
        page.start_login()
    assert page.devices[0].login_error
    assert "nepřihlášeno 1" in page.status.text()
    assert "locked" in page.log_view.toPlainText()


def test_the_page_says_whose_credentials_it_logs_in_with(page):
    page.login_settings = settings
    page._show_login_note()
    assert "bee" in page.login_note.text() and "klíč" in page.login_note.text()
    assert "122" in page.login_note.text()


# ------------------------------------------------------------------ remembered credentials


def logging_in(page, qtbot, answer) -> list[tuple[str, str]]:
    """Log in to the one listed device; ``answer(username)`` opens or raises."""
    tried: list[tuple[str, str]] = []

    def open_fn(s, host, cancelled):
        tried.append((s.params.username, s.params.password))
        return answer(s.params.username)

    page.open_fn = open_fn
    page.close_fn = lambda session: None
    page.neighbours_fn = lambda *a, **k: []
    page._show()
    select(page, 0)
    with qtbot.waitSignal(page.devicesChanged, timeout=3000):
        page.start_login()
    return tried


def refuse_all_but(user: str, inventory):
    from mqs.transport.base import AuthError

    def answer(username):
        if username != user:
            raise AuthError("invalid user name or password")
        return Opened(inventory)
    return answer


def test_a_login_remembers_what_the_device_took(page, qtbot):
    found = antenna("ap-1", address="192.0.2.9").inventory
    page.login_settings = lambda: settings("bee")
    page.devices = [guess("ap-1")]
    assert logging_in(page, qtbot, refuse_all_but("bee", found)) == [("bee", "")]
    remembered = page.credentials.find(guess("ap-1"))
    assert remembered is not None and remembered.username == "bee"
    assert "1 zařízení" in page.login_note.text()


def test_a_device_is_logged_in_with_what_it_took_last_time_first(page, qtbot):
    found = antenna("ap-1", address="192.0.2.9").inventory
    page.credentials.remember(guess("ap-1"), "other", "pw2")
    page.login_settings = lambda: settings("bee")
    page.devices = [guess("ap-1")]
    assert logging_in(page, qtbot, refuse_all_but("other", found)) == [("other", "pw2")]
    assert page.devices[0].inventory is not None
    assert "ap-1: přihlášeno jako other" in page.log_view.toPlainText()


def test_a_refused_remembered_login_falls_back_to_the_window(page, qtbot):
    found = antenna("ap-1", address="192.0.2.9").inventory
    page.credentials.remember(guess("ap-1"), "other", "old")
    page.login_settings = lambda: settings("bee")
    page.devices = [guess("ap-1")]
    tried = logging_in(page, qtbot, refuse_all_but("bee", found))
    assert tried == [("other", "old"), ("bee", "")]
    assert page.credentials.find(guess("ap-1")).username == "bee"
    assert len(page.credentials) == 1


def test_a_device_that_does_not_answer_is_not_tried_twice(page, qtbot):
    from mqs.transport.base import TransportError

    def answer(username):
        raise TransportError("timed out")

    page.credentials.remember(guess("ap-1"), "other", "pw2")
    page.login_settings = lambda: settings("bee")
    page.devices = [guess("ap-1")]
    assert logging_in(page, qtbot, answer) == [("other", "pw2")]
    assert page.devices[0].login_error
    assert page.credentials.find(guess("ap-1")).username == "other"


def test_the_remembered_credentials_can_be_forgotten(page, monkeypatch):
    page.credentials.remember(guess("ap-1"), "other", "pw2")
    page._show_login_note()
    assert page.forget_button.isEnabled()
    monkeypatch.setattr(page.dialogs, "question", lambda *a: True)
    page.forget_credentials()
    assert len(page.credentials) == 0 and not page.forget_button.isEnabled()
    assert "Uložené údaje" not in page.login_note.text()


def test_a_batch_logs_in_to_each_device_with_its_own_credentials(
        page, qtbot, tmp_path, monkeypatch):
    from test_fleet_end_to_end import Lab

    devices = chain_of_three()
    lab = Lab(tmp_path, {d.address: d for d in devices}, "7.16.1")
    page.update_dir = lab.folder
    page.load_packages()
    page.target.setCurrentText("7.16.1")
    page.credentials.remember(devices[1], "relay-admin", "relay-pw")
    page.login_settings = lambda: settings("bee")
    used: dict[str, set[str]] = {}

    def open_fn(s, host, cancelled):
        used.setdefault(host, set()).add(s.params.username)
        return lab.sessions[host]

    page.open_fn = open_fn
    page.close_fn = lambda session: None
    page.changer_factory = lab.changer
    page.backup_dir = tmp_path / "backups"
    from mqs.model.version import RosVersion
    page.read_fn = lambda session: session.inventory.model_copy(update={
        "resource": session.inventory.resource.model_copy(
            update={"version": RosVersion.parse("7.16.1")})})
    monkeypatch.setattr(page.dialogs, "question", lambda *a: True)
    page.devices = devices
    page._show()

    with qtbot.waitSignal(page.devicesChanged, timeout=10000):
        page.start_batch()
    assert "aktualizováno 3" in page.status.text()
    assert used == {"192.0.2.1": {"bee"}, "192.0.2.2": {"relay-admin"},
                    "192.0.2.3": {"bee"}}


# ------------------------------------------------------------------ the batch


def update_folder(tmp_path, *versions: str):
    from factories import make_npk

    folder = tmp_path / "Update"
    folder.mkdir(parents=True, exist_ok=True)
    for text in versions:
        parts = tuple(int(p) for p in [*text.split("."), "0", "0"][:3])
        (folder / f"routeros-{text}-arm.npk").write_bytes(make_npk("system", parts, "arm"))
        (folder / f"wireless-{text}-arm.npk").write_bytes(make_npk("wireless", parts, "arm"))
    return folder


def chain_of_three() -> list[Candidate]:
    return [antenna("core", address="192.0.2.1", mode="ap-bridge"),
            antenna("relay", address="192.0.2.2", mode="ap-bridge", parent="core"),
            antenna("leaf", address="192.0.2.3", parent="relay")]


def test_the_plan_says_when_a_board_names_no_lowest_version(page, tmp_path):
    """A CHR or a board without the field has no floor; the plan says so rather than
    leaving the line out, so a missing floor is not mistaken for a missing check."""
    update_folder(tmp_path, "7.15.3", "7.16.1")
    page.load_packages()
    device = antenna("chr", address="192.0.2.5")
    inventory = device.inventory.model_copy(update={"routerboard": None})
    page.devices = [device.model_copy(update={"inventory": inventory})]
    page._show()
    assert "nejnižší možnou verzi zařízení neuvádí" in page.order_text.toPlainText()
    assert page.table.item(0, 7).text() == ""


def test_the_target_offers_what_the_update_directory_holds(page, tmp_path):
    update_folder(tmp_path, "7.15.3", "7.16.1")
    page.load_packages()
    assert [page.target.itemText(i) for i in range(page.target.count())] == [
        "7.16.1", "7.15.3"]
    assert page.target.currentText() == "7.16.1"


def test_the_plan_names_every_file_each_device_gets(page, tmp_path):
    update_folder(tmp_path, "7.15.3", "7.16.1")
    page.load_packages()
    page.devices = chain_of_three()
    page._show()
    text = page.order_text.toPlainText()
    assert "1. leaf  (stanice, teď 7.15.3)" in text
    assert "nejnižší možná verze: 7.10" in text
    assert "→ 7.16.1: routeros-7.16.1-arm.npk, wireless-7.16.1-arm.npk" in text
    assert "3. core  (AP, teď 7.15.3, přes něj vede 2)" in text
    assert "restartů celkem 3" in page.order_hint.text()
    assert page.batch_button.isEnabled()


def test_a_device_without_its_packages_blocks_the_batch(page, tmp_path):
    update_folder(tmp_path, "7.15.3")
    (tmp_path / "Update" / "routeros-7.16.1-arm.npk").write_bytes(
        __import__("factories").make_npk("system", (7, 16, 1), "arm"))
    page.load_packages()
    page.target.setCurrentText("7.16.1")
    page.devices = chain_of_three()
    page._show()
    assert "NELZE" in page.order_text.toPlainText()
    assert "nespustí" in page.order_hint.text()
    assert not page.batch_button.isEnabled()


def test_the_selection_limits_the_batch(page, tmp_path):
    update_folder(tmp_path, "7.15.3", "7.16.1")
    page.load_packages()
    page.devices = chain_of_three()
    page._show()
    select(page, 2)
    plan = page.batch_plan()
    assert [s.name for s in plan.steps] == ["leaf"]


def test_a_batch_updates_every_device_leaves_first(page, qtbot, tmp_path, monkeypatch):
    from test_fleet_end_to_end import Lab

    devices = chain_of_three()
    lab = Lab(tmp_path, {d.address: d for d in devices}, "7.16.1")
    page.update_dir = lab.folder
    page.load_packages()
    page.target.setCurrentText("7.16.1")
    page.login_settings = settings
    page.open_fn = lambda s, host, cancelled: lab.sessions[host]
    page.close_fn = lambda session: None
    page.changer_factory = lab.changer
    page.backup_dir = tmp_path / "backups"
    from mqs.model.version import RosVersion
    page.read_fn = lambda session: session.inventory.model_copy(update={
        "resource": session.inventory.resource.model_copy(
            update={"version": RosVersion.parse("7.16.1")})})
    asked: list[str] = []
    monkeypatch.setattr(page.dialogs, "question",
                        lambda parent, title, text: asked.append(text) or True)
    page.devices = devices
    page._show()
    assert page.batch_button.isEnabled()

    with qtbot.waitSignal(page.devicesChanged, timeout=10000):
        page.start_batch()
    assert asked and "leaf, relay, core" in asked[0]
    # The version, then RouterBOOT (on by default, as on the Aplikovat page).
    assert all(router.reboots == 2 for router in lab.routers.values())
    log = page.log_view.toPlainText()
    assert log.index("OK leaf") < log.index("OK relay") < log.index("OK core")
    assert "aktualizováno 3" in page.status.text()
    assert texts(page, 6) == ["7.16.1", "7.16.1", "7.16.1"]
    assert "už má 7.16.1" in page.order_text.toPlainText()


def with_firmware(device: Candidate, current: str, waiting: str) -> Candidate:
    board = device.inventory.routerboard.model_copy(
        update={"current_firmware": current, "upgrade_firmware": waiting})
    return device.model_copy(update={
        "inventory": device.inventory.model_copy(update={"routerboard": board})})


def test_a_device_on_the_target_still_gets_its_waiting_routerboot(page, tmp_path):
    update_folder(tmp_path, "7.15.3")
    page.load_packages()
    page.devices = [with_firmware(antenna("sxt-01", address="192.0.2.5"), "7.10.0", "7.15.3"),
                    antenna("done", address="192.0.2.6")]
    page._show()
    text = page.order_text.toPlainText()
    assert "už má 7.15.3, jen RouterBOOT 7.10.0 → 7.15.3" in text
    assert "už má 7.15.3, nic se neinstaluje" in text
    assert "Do dávky jde 1 zařízení, restartů celkem 1" in page.order_hint.text()
    assert page.batch_button.isEnabled()

    page.routerboot.setChecked(False)
    assert "jen RouterBOOT" not in page.order_text.toPlainText()
    assert not page.batch_button.isEnabled()
    assert not page.routerboot_down.isEnabled()


def test_an_older_routerboot_is_written_only_when_asked(page, tmp_path):
    update_folder(tmp_path, "7.15.3")
    page.load_packages()
    page.devices = [with_firmware(antenna("sxt-01", address="192.0.2.5"), "7.23.7", "7.15.3")]
    page._show()
    assert "nic se neinstaluje" in page.order_text.toPlainText()
    assert not page.batch_button.isEnabled()

    page.routerboot_down.setChecked(True)
    assert "jen snížení RouterBOOT 7.23.7 → 7.15.3" in page.order_text.toPlainText()
    assert page.batch_button.isEnabled()


def test_a_batch_lowers_the_routerboot_of_a_device_already_on_the_target(
        page, qtbot, tmp_path, monkeypatch):
    from test_fleet_end_to_end import Lab

    device = with_firmware(antenna("sxt-01", address="192.0.2.5"), "7.23.7", "7.15.3")
    lab = Lab(tmp_path, {device.address: device}, "7.15.3")
    router = lab.routers[device.address]
    router.firmware.update({"current-firmware": "7.23.7", "upgrade-firmware": "7.15.3"})
    page.update_dir = lab.folder
    page.load_packages()
    page.target.setCurrentText("7.15.3")
    page.login_settings = settings
    page.open_fn = lambda s, host, cancelled: lab.sessions[host]
    page.close_fn = lambda session: None
    page.changer_factory = lab.changer
    page.backup_dir = tmp_path / "backups"
    page.read_fn = lambda session: with_firmware(device, "7.15.3", "7.15.3").inventory
    asked: list[str] = []
    monkeypatch.setattr(page.dialogs, "question",
                        lambda parent, title, text: asked.append(text) or True)
    page.devices = [device]
    page.routerboot_down.setChecked(True)
    page._show()
    assert page.batch_button.isEnabled()

    with qtbot.waitSignal(page.devicesChanged, timeout=10000):
        page.start_batch()
    assert asked and "Jen RouterBOOT" in asked[0] and "sxt-01" in asked[0]
    assert router.reboots == 1 and router.firmware["current-firmware"] == "7.15.3"
    assert router.packages["routeros"] == "7.15.3"  # nothing installed
    assert "aktualizováno 1" in page.status.text()
    assert "nic se neinstaluje" in page.order_text.toPlainText()


def test_an_uncertain_order_must_be_confirmed_by_typing_the_version(page, tmp_path,
                                                                    monkeypatch):
    update_folder(tmp_path, "7.15.3", "7.16.1")
    page.load_packages()
    page.login_settings = settings
    started: list[int] = []
    page.open_fn = lambda *a: started.append(1)
    prompts: list[str] = []
    monkeypatch.setattr(page.dialogs, "ask_text",
                        lambda parent, title, label, text="": prompts.append(label) or "ano")
    page.devices = [antenna("a", address="192.0.2.1"), antenna("b", address="192.0.2.2")]
    page._show()
    page.start_batch()
    assert prompts and "pořadí není jisté" in prompts[0] and "7.16.1" in prompts[0]
    assert page._token is None and started == []
