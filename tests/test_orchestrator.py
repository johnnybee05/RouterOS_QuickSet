"""Whole apply flow against the in-memory router (no device)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from ipaddress import IPv4Address, IPv4Interface, IPv4Network

import pytest

from factories import make_inventory
from fake_router import FakeRouter, RouterUploader
from fake_transport import FakeTransport
from mqs.apply.orchestrator import (
    RESET_ARM_ATTEMPTS,
    RESET_SETTLE_S,
    ApplyAborted,
    ApplyContext,
    ApplyOrchestrator,
    StepStatus,
    keep_own_first,
    reset_credentials,
)
from mqs.apply.plan import ROLLBACK_JOB, ApplyMode, ApplyOptions
from mqs.apply.reconnect import (
    ReconnectFailed,
    ReconnectTarget,
    mac_interface_after_apply,
    reconnect_hosts,
    reconnect_with_retry,
)
from mqs.generator.base import generate
from mqs.generator.context import TargetContext
from mqs.generator.hardening.access import DEFAULT_SERVICE_PORTS
from mqs.generator.ir import KeepOwnFirst, RawLine, SectionKey
from mqs.issues import codes
from mqs.model.inventory import ServiceInfo
from mqs.model.ports import rename_port
from mqs.model.presets import apply_preset
from mqs.model.profile import DeviceProfile, Mode
from mqs.model.wan import StaticWan
from mqs.transport.base import AuthError, ConnectionParams, ServiceUnavailable, TransportKind
from mqs.transport.factory import ConnectFailed, ConnectOutcome
from mqs.transport.mac_connect import MacConnectMethod, MacConnectResult, MacRoute
from mqs.transport.mactelnet.client import MacTelnetAuthError, MacTelnetError
from mqs.transport.mndp import DiscoveredDevice
from mqs.transport.netifs import LocalInterface

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
        "dhcp_clients": ["ether1"],  # the router preset takes its WAN address by DHCP
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


def test_old_backups_are_pruned_after_the_apply():
    router, orchestrator, events, _ = build(keep_backups=2)
    old = ["pre-quickset-20260901-100000", "pre-quickset-20260910-100000",
           "pre-quickset-20260914-100000"]
    for name in old:
        router.files[name + ".backup"] = "<binary>"
        router.files[name + ".rsc"] = "# export"
    router.files["my-own.backup"] = "<binary>"
    report = orchestrator.run()
    assert report.succeeded
    # The new backup and the newest old one stay; foreign files are not touched.
    assert sorted(router.files) == [
        "my-own.backup",
        "pre-quickset-20260914-100000.backup", "pre-quickset-20260914-100000.rsc",
        "pre-quickset-20260915-100000.backup", "pre-quickset-20260915-100000.rsc"]
    prune = next(e for e in events if e.step == "apply.step.prune")
    assert prune.params == {"count": 4, "keep": 2}

    router, orchestrator, _, _ = build(keep_backups=0)
    router.files["pre-quickset-20260901-100000.backup"] = "<binary>"
    orchestrator.run()
    assert "pre-quickset-20260901-100000.backup" in router.files


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

    # The router preset has the firewall, whose find and reordering the API does itself.
    router, orchestrator, events, _ = build()
    orchestrator.ctx.uploader_factory = lambda t: RouterUploader(router, fail=True,
                                                                 line_by_line=True)
    router.pending_import_effects = {}  # line-by-line does not use /import
    with pytest.raises(ApplyAborted):
        orchestrator.run()  # FakeRouter does not implement the individual commands
    assert ("apply.step.execute_lines", StepStatus.RUNNING) in [(e.step, e.status) for e in events]
    assert "/import" not in router.log

    # A line only the console understands is refused before the first line, rather than
    # halfway through the configuration.
    router, orchestrator, events, _ = build()
    section = orchestrator.ctx.result.script.section(SectionKey.SYSTEM)
    section.items.append(RawLine(':if ([:len [/ip pool find]] = 0) do={ :put x }'))
    orchestrator.ctx.uploader_factory = lambda t: RouterUploader(router, fail=True,
                                                                 line_by_line=True)
    router.pending_import_effects = {}
    with pytest.raises(ApplyAborted) as err:
        orchestrator.run()
    assert err.value.issue.code == "apply.failed.execute"
    assert "raw lines need the SSH transport" in err.value.issue.params["error"]
    assert ("apply.step.execute_lines", StepStatus.RUNNING) not in [
        (e.step, e.status) for e in events]
    assert router.state.identity == "MikroTik"


def test_keep_own_first_over_the_api():
    fake = FakeTransport({"/ip/firewall/filter/print": [
        {".id": "*1", "dynamic": "true", "comment": "special dummy rule"},
        {".id": "*2", "comment": "defconf: accept ICMP"},
        {".id": "*3"},
        {".id": "*4", "comment": "quickset: drop invalid"},
        {".id": "*5", "comment": "quickset: accept from LAN"},
    ], "/ip/firewall/filter/move": []})
    keep_own_first(fake, KeepOwnFirst("(console line)", menu="/ip firewall filter",
                                      key="comment", regex="^quickset: "))
    moves = [args for path, args in fake.calls if path == "/ip/firewall/filter/move"]
    # The others go to the end in their order; the dynamic rule cannot be moved.
    assert moves == [{"numbers": "*2"}, {"numbers": "*3"}]


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
    assert "keep-users" not in router.reset_args  # the verified command, unchanged


def test_reset_keeping_users_reconnects_as_the_same_account():
    router, orchestrator, _, reconnects = build(
        ApplyMode.RESET_AND_IMPORT, keep_users=True, confirmed_identity="MikroTik")
    router.state.admin_password = "Old"
    orchestrator.transport.params = ConnectionParams(host="192.168.88.1", username="bee",
                                                     key_filename="id_rsa")
    report = orchestrator.run()
    assert report.succeeded
    assert router.reset_args["keep-users"] == "yes"
    assert router.reset_args["no-defaults"] == "yes" and router.reset_args["skip-backup"] == "no"
    assert router.state.admin_password == "Old"  # none given, so admin keeps its own
    params = reconnects[0].params
    assert (params.username, params.password, params.key_filename) == ("bee", "", "id_rsa")


@pytest.mark.parametrize(("keep_users", "user", "given", "expected"), [
    (False, "bee", "N3w", ("admin", "N3w", None)),  # a plain reset leaves admin alone
    (True, "bee", "N3w", ("bee", "pw", "id_rsa")),  # bee is still there, key included
    (True, "admin", "N3w", ("admin", "N3w", "id_rsa")),  # the script changes admin's
    (True, "admin", "", ("admin", "pw", "id_rsa")),  # and leaves it without a new one
])
def test_who_logs_in_after_a_reset(keep_users, user, given, expected):
    params = ConnectionParams(host="h", username=user, password="pw", key_filename="id_rsa")
    options = ApplyOptions(ApplyMode.RESET_AND_IMPORT, keep_users=keep_users,
                           admin_password=given)
    after = reset_credentials(params, options)
    assert (after.username, after.password, after.key_filename) == expected


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


# ---------------------------------------------------------------- back in by MAC


MAC_DEVICE = DiscoveredDevice(mac="D0:EA:11:5D:65:E2", identity="MikroTik",
                              interface_name="bridge/ether1")
MAC_LOCAL = LocalInterface("Ethernet", IPv4Address("192.168.55.55"),
                           IPv4Network("192.168.55.0/24"), index=7)
ROUTE = MacRoute(MAC_DEVICE, MAC_LOCAL, IPv4Address("192.168.55.254"))


@pytest.mark.parametrize(("reported", "expected"), [
    ("bridge/ether1", "ether1"),  # a factory hEX S; the router profile makes ether1 the WAN
    ("ether1", "ether1"),
    ("ether3", "bridge"),  # a LAN port goes into the profile's bridge
    ("bridge/ether3", "bridge"),
    ("bridge", "bridge"),
    (None, "ether1"),
])
def test_the_lent_address_goes_where_the_port_ends_up(reported, expected):
    assert mac_interface_after_apply(_static_wan_profile(), reported) == expected


def mac_result(transport="transport", temp="192.168.55.254/24"):
    return MacConnectResult(ConnectOutcome(transport), MacConnectMethod.MAC_TELNET,
                            temp_address=temp, host="192.168.55.254")


def test_a_device_reached_by_mac_is_reached_by_mac_again():
    clock = Clock()
    calls = []

    def mac_connect(device, params, address, interface, **kwargs):
        calls.append((device, params, address, interface, kwargs))
        return mac_result()

    def connect(*args, **kwargs):
        raise AssertionError("no address is tried while MAC answers")

    target = ReconnectTarget(ConnectionParams(host="192.168.55.68", username="admin",
                                              password="N3w"), 8729, 122,
                             mac=ROUTE, mac_interface="ether1")
    result = reconnect_with_retry(target, timeout_s=60, connect=connect, mac_connect=mac_connect,
                                  sleep=clock.sleep, clock=clock)
    assert result.temp_address == "192.168.55.254/24"
    device, params, address, interface, kwargs = calls[0]
    assert (device, address, interface) == (MAC_DEVICE, ROUTE.address, MAC_LOCAL)
    assert params.password == "N3w"
    assert kwargs["api_port"] == 8729 and kwargs["ssh_port"] == 122
    assert kwargs["device_interface"] == "ether1"
    assert kwargs["connect"] is connect, "the IP half goes through the same connect"


def test_a_console_that_refuses_the_login_is_asked_again():
    """After a reset MAC-Telnet may answer before the script has set the new password."""
    clock = Clock()
    answers = [MacTelnetAuthError("Login failed, incorrect username or password"),
               MacTelnetError("no answer"), mac_result()]

    def mac_connect(*args, **kwargs):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def refuse(*args, **kwargs):
        raise ServiceUnavailable("refused")

    target = ReconnectTarget(ConnectionParams(host="192.168.55.68", username="admin"),
                             8729, 122, mac=ROUTE)
    result = reconnect_with_retry(target, timeout_s=120, connect=refuse,
                                  mac_connect=mac_connect, sleep=clock.sleep, clock=clock)
    assert result.method == MacConnectMethod.MAC_TELNET and answers == []


def test_when_mac_does_not_answer_the_addresses_still_do():
    clock = Clock()

    def mac_connect(*args, **kwargs):
        raise MacTelnetError("device did not answer")

    def connect(params, choice, api_port, ssh_port):
        return ConnectOutcome("transport")

    target = ReconnectTarget(ConnectionParams(host="192.168.55.68", username="admin"),
                             8729, 122, mac=ROUTE)
    assert reconnect_with_retry(target, timeout_s=60, connect=connect, mac_connect=mac_connect,
                                sleep=clock.sleep, clock=clock) == "transport"
    assert clock.now == 0, "the address answered in the same round"


def test_a_failed_reconnect_names_the_mac_first():
    clock = Clock()

    def silent(*args, **kwargs):
        raise MacTelnetError("device did not answer")

    def refuse(*args, **kwargs):
        raise ServiceUnavailable("refused")

    target = ReconnectTarget(ConnectionParams(host="192.168.55.68", username="admin"),
                             8729, 122, alternatives=("192.168.88.1",), mac=ROUTE)
    assert target.describe() == "D0:EA:11:5D:65:E2 (MAC), 192.168.55.68, 192.168.88.1"
    with pytest.raises(ReconnectFailed, match=r"D0:EA:11:5D:65:E2 \(MAC\), 192\.168\.55\.68"):
        reconnect_with_retry(target, timeout_s=10, connect=refuse, mac_connect=silent,
                             sleep=clock.sleep, clock=clock)


def _mac_reset_build(reached):
    """A reset of a device reached by MAC, the reconnect answering with ``reached``."""
    targets = []

    def reconnect(target):
        targets.append(target)
        return reached(router, target)

    router, orchestrator, events, _ = build(
        ApplyMode.RESET_AND_IMPORT, admin_password="N3w", confirmed_identity="MikroTik",
        reconnect=reconnect)
    orchestrator.ctx.profile = _static_wan_profile()
    orchestrator.ctx.transport.params = replace(orchestrator.ctx.transport.params,
                                                host="192.168.55.254")
    orchestrator.ctx.session_port = "ether1"
    orchestrator.ctx.temp_address = "192.168.55.254/24"
    orchestrator.ctx.mac_route = ROUTE
    return orchestrator, events, targets


def _router_session(router, target):
    return router.session(port=target.api_port, username=target.params.username,
                          password=target.params.password)


def test_a_reset_by_mac_comes_back_by_mac_with_a_new_lent_address():
    """The hardware report of 2026-09-21: after the apply the reconnect went to IP
    addresses only, although the session had been opened by MAC."""

    def by_mac(router, target):
        return MacConnectResult(ConnectOutcome(_router_session(router, target)),
                                MacConnectMethod.MAC_TELNET,
                                temp_address="192.168.55.254/24", host="192.168.55.254")

    orchestrator, events, targets = _mac_reset_build(by_mac)
    report = orchestrator.run()
    target = targets[0]
    assert target.mac == ROUTE
    assert target.mac_interface == "ether1", "ether1 left the factory bridge for the WAN"
    assert target.params.username == "admin" and target.params.password == "N3w"
    assert report.temp_address == "192.168.55.254/24", "lent again, to give back again"
    assert report.mac_method == "mac-telnet"
    running = next(e for e in events if e.step == "apply.step.reconnect"
                   and e.status == StepStatus.RUNNING)
    assert running.params["hosts"].startswith("D0:EA:11:5D:65:E2 (MAC), 192.168.55.68")
    done = next(e for e in events if e.step == "apply.step.reconnect"
                and e.status == StepStatus.OK)
    assert done.params["hosts"].startswith("D0:EA:11:5D:65:E2 (mac-telnet")


def test_a_reset_by_mac_that_comes_back_by_address_lends_nothing():
    orchestrator, _, _ = _mac_reset_build(_router_session)
    report = orchestrator.run()
    assert report.temp_address is None and report.mac_method is None


def test_a_renamed_session_port_is_followed_after_the_apply():
    """MNDP and the session port name the port as it was; the profile renames it."""
    orchestrator, _, targets = _mac_reset_build(_router_session)
    profile = rename_port(_static_wan_profile(), "ether1", "internet")
    orchestrator.ctx.profile = profile
    orchestrator.ctx.result = generate(profile,
                                       TargetContext.from_inventory(make_inventory("7.15.3",
                                                                                   PORTS)))
    assert orchestrator.ctx.result.renames == {"ether1": "internet"}
    orchestrator.run()
    assert targets[0].mac_interface == "internet"
    assert targets[0].params.host == "192.168.55.68", "the WAN address of the session port"
    lan3 = rename_port(profile, "ether3", "lan3")
    assert mac_interface_after_apply(lan3, "bridge/ether3", {"ether3": "lan3"}) == "bridge"


def test_the_lent_address_follows_a_port_mndp_announces_by_an_earlier_name():
    """On the hEX S MNDP kept the name a port had when it began announcing (2026-09-24)."""
    orchestrator, _, targets = _mac_reset_build(_router_session)
    inventory = make_inventory("7.15.3", ["uplink", *PORTS[1:]],
                               default_names={"uplink": "ether1"})
    profile = rename_port(_static_wan_profile(), "ether1", "internet")
    orchestrator.ctx.profile, orchestrator.ctx.inventory = profile, inventory
    orchestrator.ctx.session_port = "uplink"
    orchestrator.ctx.result = generate(profile, TargetContext.from_inventory(inventory))
    assert orchestrator.ctx.result.renames == {"uplink": "internet"}
    assert ROUTE.device.interface_name == "bridge/ether1"  # the factory name, not "uplink"
    orchestrator.run()
    assert targets[0].mac_interface == "internet"
