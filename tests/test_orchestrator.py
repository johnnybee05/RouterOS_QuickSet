"""Whole apply flow against the in-memory router (no device)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from ipaddress import IPv4Address, IPv4Interface

import pytest

from factories import make_inventory
from fake_router import FakeRouter, RouterUploader
from mqs.apply.orchestrator import (
    RESET_ARM_ATTEMPTS,
    RESET_SETTLE_S,
    ApplyAborted,
    ApplyContext,
    ApplyOrchestrator,
    StepStatus,
)
from mqs.apply.plan import ROLLBACK_JOB, ApplyMode, ApplyOptions
from mqs.apply.reconnect import (
    ReconnectFailed,
    ReconnectTarget,
    reconnect_hosts,
    reconnect_with_retry,
)
from mqs.generator.base import generate
from mqs.generator.context import TargetContext
from mqs.generator.hardening.access import DEFAULT_SERVICE_PORTS
from mqs.issues import codes
from mqs.model.inventory import ServiceInfo
from mqs.model.presets import apply_preset
from mqs.model.profile import DeviceProfile, Mode
from mqs.model.wan import StaticWan
from mqs.transport.base import AuthError, ConnectionParams, ServiceUnavailable, TransportKind
from mqs.transport.factory import ConnectFailed, ConnectOutcome

PORTS = ["ether1", "ether2", "ether3", "ether4", "ether5"]
NOW = datetime(2026, 9, 15, 10, 0, 0)


def build(mode=ApplyMode.IMPORT, *, kind=TransportKind.API_SSL, port=8729, uploader=None,
          reconnect=None, **options):
    router = FakeRouter()
    profile = apply_preset(DeviceProfile(identity="PV-R1"), Mode.ROUTER, PORTS)
    inventory = make_inventory("7.15.3", PORTS)
    result = generate(profile, TargetContext.from_inventory(inventory))
    router.pending_import_effects = {
        "identity": "PV-R1",
        "ssh_port": result.access.ssh_port,
        "services_disabled": {n for n, s in result.access.services.items() if s.disabled},
        "bridges": {"bridge": "rstp"},
        "addresses": [("192.168.88.1/24", "bridge")],
        "ports": profile.bridge.ports,
        "admin_password": options.get("admin_password", ""),
    }
    session = router.session(port=port, kind=kind)
    events = []
    reconnects: list[ReconnectTarget] = []

    def default_reconnect(target):
        reconnects.append(target)
        return router.session(port=target.api_port, username=target.params.username,
                              password=target.params.password)

    ctx = ApplyContext(
        profile=profile, result=result, options=ApplyOptions(mode, **options),
        inventory=inventory, transport=session,
        uploader_factory=lambda transport: uploader or RouterUploader(router),
        reconnect=reconnect or default_reconnect, now=lambda: NOW, on_event=events.append,
    )
    return router, ApplyOrchestrator(ctx), events, reconnects


def test_import_over_api_ssl_happy_path(tmp_path):
    router, orchestrator, events, reconnects = build(download_dir=tmp_path)
    report = orchestrator.run()
    assert report.succeeded, [c.message for c in report.checks if not c.ok]
    assert router.state.identity == "PV-R1" and router.state.ssh_port == 122
    assert router.log.index("/system/backup/save") < router.log.index("/import")
    assert "pre-quickset-20260915-100000.backup" in router.files
    assert sorted(p.name for p in report.downloaded) == [
        "pre-quickset-20260915-100000.backup", "pre-quickset-20260915-100000.rsc"]
    assert "quickset-20260915-100000.rsc" not in router.files  # cleaned up
    assert reconnects == []  # api-ssl session survives the SSH port change
    steps = [(e.step, e.status) for e in events]
    assert ("apply.step.reconnect", StepStatus.SKIPPED) in steps
    assert steps[-1] == ("apply.step.cleanup", StepStatus.OK)
    assert all("{" not in e.message for e in events)


def test_import_over_ssh_reconnects_to_new_port():
    _, orchestrator, events, reconnects = build(kind=TransportKind.SSH, port=22)
    report = orchestrator.run()
    assert report.connection_lost_during == "execute"
    assert reconnects[0].ssh_port == 122 and reconnects[0].api_port == 8729
    assert report.transport is not None and report.transport.port == 8729
    assert report.succeeded
    assert ("apply.connection_lost", StepStatus.WARNING) in [(e.step, e.status) for e in events]


def test_lost_connection_that_cannot_be_restored_is_reported():
    def failing(target):
        raise ReconnectFailed(target, 7, "connection refused")

    _, orchestrator, events, _ = build(kind=TransportKind.SSH, port=22, reconnect=failing)
    with pytest.raises(ApplyAborted) as err:
        orchestrator.run()
    issue = err.value.issue
    assert issue.code == "apply.failed.reconnect"
    assert issue.params["attempts"] == 7 and issue.params["ssh_port"] == 122
    assert "SSH 122" in issue.message
    assert events[-1].status == StepStatus.FAILED


def test_backup_failure_changes_nothing():
    router, orchestrator, _, _ = build()
    router.fail["/system/backup/save"] = "not enough space"
    with pytest.raises(ApplyAborted) as err:
        orchestrator.run()
    assert err.value.issue.code == "apply.failed.backup"
    assert "/import" not in router.log and router.state.identity == "MikroTik"


def test_download_failure_is_only_a_warning(tmp_path):
    router, orchestrator, _, _ = build(download_dir=tmp_path,
                                       uploader=None)
    orchestrator.ctx.uploader_factory = lambda t: RouterUploader(router, download_fail=True)
    report = orchestrator.run()
    assert "apply.warning.download" in codes(report.issues)
    assert report.succeeded


def test_upload_failure_and_line_by_line_fallback():
    router, orchestrator, _, _ = build()
    orchestrator.ctx.uploader_factory = lambda t: RouterUploader(router, fail=True)
    with pytest.raises(ApplyAborted) as err:
        orchestrator.run()
    assert err.value.issue.code == "apply.failed.upload"

    router, orchestrator, events, _ = build()
    orchestrator.ctx.uploader_factory = lambda t: RouterUploader(router, fail=True,
                                                                 line_by_line=True)
    router.pending_import_effects = {}  # line-by-line does not use /import
    with pytest.raises(ApplyAborted):
        orchestrator.run()  # FakeRouter does not implement the individual commands
    assert ("apply.step.execute_lines", StepStatus.RUNNING) in [(e.step, e.status) for e in events]
    assert "/import" not in router.log


def test_reset_requires_file_not_line_by_line():
    router, orchestrator, _, _ = build(ApplyMode.RESET_AND_IMPORT, admin_password="x",
                                       confirmed_identity="MikroTik")
    orchestrator.ctx.uploader_factory = lambda t: RouterUploader(router, fail=True,
                                                                 line_by_line=True)
    with pytest.raises(ApplyAborted) as err:
        orchestrator.run()
    assert err.value.issue.code == "apply.failed.upload_reset_needs_file"


def test_reset_flow_reconnects_as_admin_with_new_password():
    router, orchestrator, _, reconnects = build(
        ApplyMode.RESET_AND_IMPORT, admin_password="N3w", confirmed_identity="MikroTik")
    report = orchestrator.run()
    assert router.reboots == 1
    assert router.run_after_reset == "quickset-20260915-100000.rsc"
    assert router.state.identity == "PV-R1" and router.state.admin_password == "N3w"
    assert reconnects[0].params.username == "admin" and reconnects[0].params.password == "N3w"
    assert report.connection_lost_during == "reset"
    assert report.succeeded
    assert "quickset-20260915-100000.rsc" not in router.files  # removed by the script itself
    assert not report.rollback_pending


def test_import_error_with_rollback_pending_is_explained():
    router, orchestrator, _, _ = build(rollback_minutes=5)
    router.fail["/import"] = "failure: already have interface with such name (line 12)"
    with pytest.raises(ApplyAborted) as err:
        orchestrator.run()
    assert err.value.issue.code == "apply.failed.execute"
    assert "already have interface" in err.value.issue.message
    assert "apply.rollback_pending" in codes(err.value.report.issues)


def test_rollback_restores_backup_exactly_once_without_loop():
    router, orchestrator, events, _ = build(rollback_minutes=5)
    report = orchestrator.run()
    assert report.rollback_pending and ROLLBACK_JOB in router.state.scheduler
    backup = router.backups["pre-quickset-20260915-100000"]
    assert ROLLBACK_JOB not in backup.scheduler  # backup was taken before the job existed
    assert router.log.index("/system/backup/save") < router.log.index("/system/scheduler/add")
    assert events[-1].step == "apply.rollback_pending"

    router.advance(4)
    assert router.reboots == 0 and router.state.identity == "PV-R1"
    router.advance(2)  # user never confirmed
    assert router.reboots == 1
    assert router.state.identity == "MikroTik" and router.state.ssh_port == 22
    assert router.state.scheduler == {}
    router.advance(60 * 24)
    assert router.reboots == 1  # no loop


def test_rollback_job_is_gone_even_if_backup_load_fails():
    router, orchestrator, _, _ = build(rollback_minutes=5)
    orchestrator.run()
    router.backups.clear()
    router.advance(10)
    assert router.state.scheduler == {} and router.reboots == 0
    assert "backup load failed pre-quickset-20260915-100000" in router.log


def test_confirm_cancels_rollback():
    router, orchestrator, events, _ = build(rollback_minutes=5)
    report = orchestrator.run()
    orchestrator.confirm()
    assert not report.rollback_pending and router.state.scheduler == {}
    router.advance(30)
    assert router.reboots == 0 and router.state.identity == "PV-R1"
    assert events[-1].step == "apply.step.confirm"
    orchestrator.confirm()  # idempotent


def test_confirm_failure_is_reported():
    router, orchestrator, _, _ = build(rollback_minutes=5)
    orchestrator.run()
    router.fail["/system/scheduler/remove"] = "timeout"
    with pytest.raises(ApplyAborted) as err:
        orchestrator.confirm()
    assert err.value.issue.code == "apply.failed.confirm"


def test_rollback_schedule_failure_aborts_before_changes():
    router, orchestrator, _, _ = build(rollback_minutes=5)
    router.fail["/system/clock/print"] = "bad command name"
    with pytest.raises(ApplyAborted) as err:
        orchestrator.run()
    assert err.value.issue.code == "apply.failed.rollback"
    assert "/import" not in router.log


def test_verify_detects_offload_not_running():
    router, orchestrator, _, _ = build()
    router.offload_running = False
    orchestrator.ctx.result = generate(orchestrator.ctx.profile, TargetContext.from_inventory(
        make_inventory("7.15.3", PORTS, switches={"switch1": "QCA8337"},
                       port_switch=dict.fromkeys(PORTS, "switch1"))))
    report = orchestrator.run()
    assert not report.succeeded
    assert "verify.offload_not_active" in codes(report.issues)


def test_cancel_before_changes_and_generate_only_rejected():
    router, orchestrator, _, _ = build()
    orchestrator.ctx.cancelled = lambda: True
    with pytest.raises(ApplyAborted) as err:
        orchestrator.run()
    assert err.value.issue.code == "apply.cancelled" and router.log == []
    with pytest.raises(ValueError):
        build(ApplyMode.GENERATE_ONLY)


def test_verify_and_cleanup_failures():
    router, orchestrator, _, _ = build()
    router.fail["/file/remove"] = "no such item"
    report = orchestrator.run()
    assert "apply.warning.cleanup" in codes(report.issues)

    router, orchestrator, _, _ = build()
    router.fail["/ip/service/print"] = "timeout"
    with pytest.raises(ApplyAborted) as err:
        orchestrator.run()
    assert err.value.issue.code == "apply.failed.verify"


def test_session_dropped_before_verify_triggers_reconnect():
    router, orchestrator, _, reconnects = build()
    router.drop_on.add("/system/identity/print")
    original = router.session

    def reconnect(target):
        reconnects.append(target)
        router.drop_on.clear()
        return original(port=target.api_port)

    orchestrator.ctx.reconnect = reconnect
    report = orchestrator.run()
    assert len(reconnects) == 1 and report.connection_lost_during == "verify"


# ---------------------------------------------------------------- reconnect_with_retry


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_reconnect_retries_until_success():
    clock = Clock()
    outcomes = [ConnectFailed([]), ServiceUnavailable("refused"), "transport"]
    seen = []

    def connect(params, choice, api_port, ssh_port):
        seen.append((api_port, ssh_port))
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return ConnectOutcome(outcome)

    attempts = []
    target = ReconnectTarget(ConnectionParams(host="h", username="u"), 8729, 122)
    transport = reconnect_with_retry(target, timeout_s=60, connect=connect, sleep=clock.sleep,
                                     clock=clock, on_attempt=lambda n, e: attempts.append(n))
    assert transport == "transport"
    assert seen == [(8729, 122)] * 3 and attempts == [1, 2]
    assert clock.now == pytest.approx(2.0 + 3.0)


def test_reconnect_gives_up_and_auth_is_fatal():
    clock = Clock()

    def refuse(*args, **kwargs):
        raise ServiceUnavailable("refused")

    target = ReconnectTarget(ConnectionParams(host="h", username="u"), 8729, 122)
    with pytest.raises(ReconnectFailed) as err:
        reconnect_with_retry(target, timeout_s=20, connect=refuse, sleep=clock.sleep, clock=clock)
    assert err.value.attempts >= 3 and "refused" in err.value.last_error

    def auth(*args, **kwargs):
        raise AuthError("bad password")

    with pytest.raises(AuthError):
        reconnect_with_retry(target, timeout_s=20, connect=auth, sleep=clock.sleep, clock=clock)

    from mqs.transport.base import Cancelled
    with pytest.raises(Cancelled):
        reconnect_with_retry(target, timeout_s=60, connect=refuse, sleep=clock.sleep, clock=clock,
                             cancelled=lambda: True)


def _reset_build():
    return build(ApplyMode.RESET_AND_IMPORT, admin_password="N3w",
                 confirmed_identity="MikroTik")


def test_reset_waits_for_the_script_to_settle_before_arming():
    """RouterOS lists an SFTP-written file at once, with its final size, and still
    refuses it for run-after-reset for a moment. Verified on 7.20.7: the same 2011-byte
    script was refused 0.02 s after the upload and accepted 3.01 s after it."""
    router, orchestrator, _, _ = _reset_build()
    orchestrator.ctx.sleep = lambda seconds: router.log.append(f"slept {seconds}")
    orchestrator.run()
    assert router.log.index(f"slept {RESET_SETTLE_S}") < \
        router.log.index("/system/reset-configuration")


def test_a_device_that_is_not_ready_yet_is_asked_again():
    router, orchestrator, _, _ = _reset_build()
    router.reset_refusals = 2  # "cannot use file", then accepted
    report = orchestrator.run()
    assert router.log.count("/system/reset-configuration") == 3
    assert router.reboots == 1 and report.succeeded
    assert "apply.reset_retry" in codes(report.issues)


def test_a_device_that_never_takes_the_script_is_reported():
    router, orchestrator, _, _ = _reset_build()
    router.reset_refusals = 99
    with pytest.raises(ApplyAborted) as err:
        orchestrator.run()
    assert err.value.issue.code == "apply.failed.execute"
    assert router.log.count("/system/reset-configuration") == RESET_ARM_ATTEMPTS
    assert router.reboots == 0, "the device was never reset"


def test_another_reset_error_is_not_retried():
    """Only "cannot use file" is worth waiting out; anything else will not improve."""
    router, orchestrator, _, _ = _reset_build()
    router.fail["/system/reset-configuration"] = "no such item"
    with pytest.raises(ApplyAborted):
        orchestrator.run()
    assert router.log.count("/system/reset-configuration") == 1


def _static_wan_profile():
    """Router preset, but with a WAN address that can be predicted after the apply."""
    profile = apply_preset(DeviceProfile(identity="R1"), Mode.ROUTER, PORTS)
    wan = profile.wan.model_copy(update={
        "settings": StaticWan(address=IPv4Interface("192.168.55.68/24"),
                              gateway=IPv4Address("192.168.55.1"))})
    return profile.model_copy(update={"wan": wan})


def test_reconnect_hosts_lead_with_where_our_own_port_will_answer():
    """Connect-by-MAC reaches a device on a temporary address, and the reset wipes it, so
    the address in use is the one certain not to answer afterwards."""
    hosts = reconnect_hosts(_static_wan_profile(), current_host="192.168.55.254",
                            session_port="ether1", expiring=["192.168.55.254/24"])
    assert hosts[0] == "192.168.55.68", "the address ether1 carries once applied"
    assert hosts[-1] == "192.168.55.254", "the wiped address is tried last, not dropped"
    assert "192.168.88.1" in hosts, "the LAN address is worth a try as well"


def test_reconnect_hosts_on_a_bridge_port_lead_with_the_lan_address():
    profile = apply_preset(DeviceProfile(), Mode.ROUTER, PORTS)  # DHCP WAN
    hosts = reconnect_hosts(profile, current_host="192.168.55.254", session_port="ether3",
                            expiring=["192.168.55.254"])
    assert hosts[0] == "192.168.88.1"
    assert hosts[-1] == "192.168.55.254"


def test_reconnect_hosts_keep_the_current_address_when_nothing_better_is_known():
    profile = apply_preset(DeviceProfile(), Mode.ROUTER, PORTS)
    hosts = reconnect_hosts(profile, current_host="10.9.9.9", session_port="ether1")
    assert hosts[0] == "10.9.9.9", "a DHCP WAN address cannot be predicted"
    assert hosts == list(dict.fromkeys(hosts)), "no address is tried twice"


def test_reconnect_sweeps_every_address_before_backing_off():
    clock = Clock()
    tried = []

    def connect(params, choice, api_port, ssh_port):
        tried.append(params.host)
        if params.host != "10.0.0.2":
            raise ServiceUnavailable("refused")
        return ConnectOutcome("transport")

    target = ReconnectTarget(ConnectionParams(host="10.0.0.1", username="u"), 8729, 122,
                             alternatives=("10.0.0.2",))
    assert reconnect_with_retry(target, timeout_s=60, connect=connect, sleep=clock.sleep,
                                clock=clock) == "transport"
    assert tried == ["10.0.0.1", "10.0.0.2"]
    assert clock.now == 0, "the second address answered in the same round, so no backoff"


def test_a_refused_login_does_not_stop_the_sweep():
    """That address may belong to something else by now. Only when nothing answers at all
    is the refused login the more useful thing to report."""
    clock = Clock()
    tried = []

    def connect(params, choice, api_port, ssh_port):
        tried.append(params.host)
        if params.host == "10.0.0.1":
            raise AuthError("bad password")
        raise ServiceUnavailable("refused")

    target = ReconnectTarget(ConnectionParams(host="10.0.0.1", username="u"), 8729, 122,
                             alternatives=("10.0.0.2",))
    with pytest.raises(AuthError):
        reconnect_with_retry(target, timeout_s=60, connect=connect, sleep=clock.sleep, clock=clock)
    assert tried == ["10.0.0.1", "10.0.0.2"]


def test_reset_does_not_lead_with_the_address_it_wipes():
    """The whole flow: reaching the device by MAC and then resetting used to send the
    reconnect straight at the temporary address, which the reset had just removed."""
    _, orchestrator, _, reconnects = build(
        ApplyMode.RESET_AND_IMPORT, admin_password="N3w", confirmed_identity="MikroTik")
    orchestrator.ctx.transport.params = replace(orchestrator.ctx.transport.params,
                                                host="192.168.55.254")
    orchestrator.ctx.session_port = "ether2"  # a bridge member, so the LAN address applies
    orchestrator.ctx.temp_address = "192.168.55.254/24"
    orchestrator.run()
    assert reconnects[0].hosts[0] == "192.168.88.1"
    assert reconnects[0].hosts[-1] == "192.168.55.254"


def test_one_transfer_connection_released_before_the_reset(tmp_path):
    """The reset is issued with no transfer session left open.

    Download and upload used to open a connection each, and the download's stayed open
    for the whole flow. On 7.20.7 the same reset call is accepted from a process holding
    no SSH session and answered "cannot use file" from the application, which holds one;
    the link is unproven (docs/DIALECT_NOTES.md), but the flow now opens one connection
    and lets it go before the reset.
    """
    router, orchestrator, _events, _ = build(ApplyMode.RESET_AND_IMPORT, admin_password="N3w",
                                             download_dir=tmp_path)
    log_at_release: list[list[str]] = []

    class RecordingUploader(RouterUploader):
        def release(self):
            log_at_release.append(list(router.log))

    made: list[RouterUploader] = []

    def factory(transport):
        made.append(RecordingUploader(router))
        return made[-1]

    orchestrator.ctx.uploader_factory = factory
    report = orchestrator.run()
    assert len(made) == 1, "the flow opens more than one transfer connection"
    assert log_at_release, "the transfer connection is never let go of"
    assert "/system/reset-configuration" not in log_at_release[0], \
        "the reset was issued while a transfer connection was still open"
    assert report.succeeded


def test_a_reset_is_checked_against_factory_services():
    """The hardware run of 2026-09-21: ssh was off before the reset and, the script only
    moving its port, rightly on afterwards - and the checklist reported it as wrong."""
    router, orchestrator, _, reconnects = build(
        ApplyMode.RESET_AND_IMPORT, admin_password="N3w", confirmed_identity="MikroTik")
    services = [ServiceInfo(name=name, port=port, disabled=name in ("ssh", "www-ssl"))
                for name, port in DEFAULT_SERVICE_PORTS.items()]
    ssh_off = make_inventory("7.15.3", PORTS, services=services)
    orchestrator.ctx.result = generate(orchestrator.ctx.profile,
                                       TargetContext.from_inventory(ssh_off))
    # Factory defaults, then what the script switches off.
    router.pending_import_effects["services_disabled"] = {"ftp", "telnet", "www", "api",
                                                          "www-ssl"}
    report = orchestrator.run()
    check = next(c for c in report.checks if c.code == "verify.services")
    assert check.ok, check.message
    assert reconnects[0].ssh_port == 122


@pytest.mark.parametrize(("mode", "left"), [
    (ApplyMode.IMPORT, "192.168.55.254/24"),  # still there, to give back on hang-up
    (ApplyMode.RESET_AND_IMPORT, None),  # the reset took it along
])
def test_the_report_says_whether_the_lent_address_survived(mode, left):
    _, orchestrator, _, _ = build(mode, admin_password="N3w", confirmed_identity="MikroTik")
    orchestrator.ctx.temp_address = "192.168.55.254/24"
    assert orchestrator.run().temp_address == left
