"""Controller, highlighter, workers and common widgets (headless Qt)."""

from __future__ import annotations

import logging

import pytest
from pydantic import SecretStr
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QLineEdit

from factories import FIVE_PORTS, make_inventory, router_profile
from mqs.issues import Issue, Severity
from mqs.model.ports import rename_port
from mqs.model.profile import DeviceProfile
from mqs.model.wan import PppoeWan
from mqs.ui.controller import ProfileController
from mqs.ui.highlighter import RouterOsHighlighter
from mqs.ui.widgets.common import (
    IssueList,
    LogView,
    PortSelector,
    QtLogHandler,
    apply_field_issues,
    set_field_error,
)
from mqs.ui.workers import CancelToken, TaskRunner

pytestmark = pytest.mark.qt


def test_controller_offline_and_online(qtbot):
    controller = ProfileController(debounce_ms=10)
    received = []
    controller.analysisReady.connect(received.append)
    controller.set_profile(router_profile(FIVE_PORTS, identity="R1"))
    qtbot.waitUntil(lambda: bool(received), timeout=2000)
    analysis = received[-1]
    assert set(analysis.results) == {6, 7}
    assert analysis.primary_major == 7 and not analysis.online
    assert analysis.can_apply, [str(i) for i in analysis.all_issues if i.severity == "error"]

    controller.set_offline_target("6.49.10", ["ether1", "ether2"])
    assert controller.primary_major == 6
    assert controller.ports() == ["ether1", "ether2"]
    assert controller.recompute().primary.target.major == 6

    inventory = make_inventory("7.15.3", FIVE_PORTS)
    controller.set_inventory(inventory, {"fasttrack_hw_offload": True})
    assert controller.context(7).online and controller.context(7).probes
    assert not controller.context(6).online
    assert controller.ports() == FIVE_PORTS
    assert controller.recompute().online


def test_controller_masks_preview_but_not_apply():
    controller = ProfileController()
    profile = router_profile(FIVE_PORTS)
    wan = profile.wan.model_copy(update={"settings": PppoeWan(username="u",
                                                              password=SecretStr("tajne"))})
    controller.set_profile(profile.model_copy(update={"wan": wan}))
    analysis = controller.recompute()
    assert "tajne" not in analysis.primary.text
    assert "password=tajne" in controller.generate_for_apply().text


def test_controller_edit_source_and_errors(qtbot):
    controller = ProfileController()
    seen = []
    controller.profileChanged.connect(lambda profile, source: seen.append(source))
    marker = object()
    controller.edit(lambda p: p.model_copy(update={"identity": ""}), source=marker)
    assert seen == [marker]
    assert not controller.recompute().can_apply
    assert controller.analysis is not None
    assert isinstance(controller.profile, DeviceProfile)


@pytest.mark.parametrize(
    ("line", "position", "key"),
    [
        ("/ip firewall filter", 3, "menu"),
        ("add chain=forward action=accept", 1, "verb"),
        ("add chain=forward action=accept", 5, "key"),
        ('set comment="hello world"', 16, "string"),
        ("set [ find default=yes ] disabled=yes", 7, "find"),
        ("# just a comment set x=1", 10, "comment"),
        (":delay 5s", 2, "scripting"),
        ("/ip service set ssh port=122", 12, "verb"),
    ],
)
def test_highlighter_rules(qtbot, line, position, key):
    document = QTextDocument(line)  # keep a reference: the highlighter is its child
    highlighter = RouterOsHighlighter(document)
    assert highlighter.format_key_at(line, position) == key
    highlighter.set_dark(True)


def test_task_runner_success_failure_progress(qtbot):
    runner = TaskRunner()
    results, errors, progress = [], [], []

    def task(token, report):
        report("half")
        return 42

    with qtbot.waitSignal(runner.busyChanged, timeout=3000):
        runner.start(task, on_finished=results.append, on_progress=progress.append)
    qtbot.waitUntil(lambda: results == [42] and not runner.busy, timeout=3000)
    assert progress == ["half"]

    def failing(token, report):
        raise RuntimeError("boom")

    runner.start(failing, on_failed=errors.append)
    qtbot.waitUntil(lambda: bool(errors) and not runner.busy, timeout=3000)
    assert str(errors[0]) == "boom"
    assert runner.wait_all()


def test_task_runner_cancel(qtbot):
    runner = TaskRunner()
    results = []

    def slow(token, report):
        while not token.cancelled():
            pass
        return "stopped"

    runner.start(slow, on_finished=results.append)
    runner.cancel_all()
    qtbot.waitUntil(lambda: results == ["stopped"], timeout=3000)


def test_cancel_token_callbacks():
    token = CancelToken()
    calls = []
    token.on_cancel(lambda: calls.append("a"))
    token.on_cancel(lambda: (_ for _ in ()).throw(OSError("socket already closed")))
    token.cancel()
    token.cancel()
    assert calls == ["a"] and token.cancelled()
    token.on_cancel(lambda: calls.append("late"))
    assert calls == ["a", "late"]


def test_field_errors(qtbot):
    edit = QLineEdit()
    qtbot.addWidget(edit)
    edit.setToolTip("původní")
    set_field_error(edit, "špatně")
    assert edit.property("fieldError") is True and edit.toolTip() == "špatně"
    set_field_error(edit, None)
    assert edit.property("fieldError") is False and edit.toolTip() == "původní"

    other = QLineEdit()
    qtbot.addWidget(other)
    issues = [Issue("identity.empty", Severity.ERROR, "identity"),
              Issue("bridge.no_ports", Severity.INFO, "bridge.ports")]
    apply_field_issues({"identity": edit, "bridge.ports": other}, issues)
    assert edit.property("fieldError") is True and other.property("fieldError") is False


def test_issue_list_sorted_and_deduplicated(qtbot):
    widget = IssueList()
    qtbot.addWidget(widget)
    info = Issue("dns.remote_requests_enabled", Severity.INFO)
    error = Issue("identity.empty", Severity.ERROR)
    widget.set_issues([info, error, error])
    assert widget.count() == 2
    assert widget.item(0).data(Qt.ItemDataRole.UserRole) == error


def test_port_selector(qtbot):
    selector = PortSelector()
    qtbot.addWidget(selector)
    emitted = []
    selector.selectionChanged2.connect(emitted.append)
    selector.set_ports(["ether1", "ether2", "ether3"], ["ether1", "ether2"], {"ether1": "WAN"})
    assert selector.checked_ports() == ["ether2"]
    assert emitted == []
    assert not selector.item(0).flags() & Qt.ItemFlag.ItemIsEnabled
    selector.item(2).setCheckState(Qt.CheckState.Checked)
    assert emitted[-1] == ["ether2", "ether3"]


def test_log_view_and_handler(qtbot):
    view = LogView()
    qtbot.addWidget(view)
    handler = QtLogHandler(view)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("mqs.ui-test")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.info("připojeno")
    logger.removeHandler(handler)
    qtbot.waitUntil(lambda: "INFO připojeno" in view.toPlainText(), timeout=2000)


def test_controller_takes_over_the_device_port_names():
    controller = ProfileController(debounce_ms=10)
    controller.set_profile(router_profile(FIVE_PORTS))  # WAN ether1, bridge ether2..5
    sources = []
    controller.profileChanged.connect(lambda profile, source: sources.append(source))
    ports = ["uplink", *FIVE_PORTS[1:]]
    controller.set_inventory(make_inventory("7.20.7", ports, default_names={"uplink": "ether1"}))
    assert sources == [None], "every page reloads the adopted profile"
    assert controller.profile.wan.interface == "uplink"
    assert controller.profile.interface_names == {"ether1": "uplink"}
    assert controller.ports() == ports
    assert controller.default_names() == {"uplink": "ether1"}
    assert controller.port_rows()[:2] == [("ether1", "uplink", "uplink"),
                                          ("ether2", "ether2", "ether2")]
    # A profile opened while connected is fitted to the device as well.
    controller.set_profile(router_profile(FIVE_PORTS), source="page")
    assert controller.profile.wan.interface == "uplink" and sources[-1] is None
    # A fresh profile gets the device's names for its ports, not the old profile's.
    assert controller.ports(DeviceProfile()) == ports


def test_controller_offline_port_names():
    controller = ProfileController()
    controller.set_profile(rename_port(router_profile(FIVE_PORTS), "ether2", "lan2"))
    assert controller.ports() == ["ether1", "lan2", "ether3", "ether4", "ether5"]
    assert controller.port_rows()[1] == ("ether2", "lan2", None)
    controller.set_offline_target(ports=["ether1"])
    # A port the target lacks is still listed, so its name can be seen and changed.
    assert controller.port_rows() == [("ether1", "ether1", None), ("ether2", "lan2", None)]
