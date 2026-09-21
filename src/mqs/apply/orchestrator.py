"""Apply flow: backup → download → upload → rollback guard → import/reset → reconnect →
verify → cleanup. Everything network-related is injected, so the whole flow runs against
an in-memory router in tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from mqs.apply.plan import (
    ROLLBACK_JOB,
    ApplyMode,
    ApplyNames,
    ApplyOptions,
    build_upload_script,
    make_names,
    result_for_mode,
)
from mqs.apply.reconnect import (
    ReconnectFailed,
    ReconnectTarget,
    mac_interface_after_apply,
    reconnect_hosts,
)
from mqs.apply.rollback import cancel_rollback, schedule_rollback
from mqs.apply.verify import CheckResult, verify_configuration
from mqs.generator.base import GenerationResult
from mqs.generator.ir import Command, Find, RawLine, cmd
from mqs.generator.render import render_inline
from mqs.issues import Issue, Severity
from mqs.messages_cs import format_message
from mqs.model.inventory import DeviceInventory
from mqs.model.profile import DeviceProfile
from mqs.transport.base import (
    Cancelled,
    CommandError,
    ConnectionLost,
    Transport,
    TransportError,
)
from mqs.transport.mac_connect import MacConnectResult, MacRoute
from mqs.transport.upload import FileUploader, UploadFailed, UploadResult, UploadStrategy

# The device needs a moment after an SFTP write before it will arm the script for
# run-after-reset, and a refused reset costs nothing, so it is simply asked again.
RESET_SETTLE_S = 3.0
RESET_ARM_ATTEMPTS = 5
FILE_NOT_READY = "cannot use file"


class StepStatus(StrEnum):
    RUNNING = "running"
    OK = "ok"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ApplyEvent:
    step: str  # message code, e.g. "apply.step.backup"
    status: StepStatus
    params: dict[str, Any] = field(default_factory=dict)
    detail: str = ""  # technical detail for the log

    @property
    def message(self) -> str:
        return format_message(self.step, self.params)


@dataclass
class ApplyReport:
    names: ApplyNames
    transport: Transport
    upload: UploadResult | None = None
    downloaded: list[Path] = field(default_factory=list)
    rollback_at: datetime | None = None
    checks: list[CheckResult] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    connection_lost_during: str | None = None
    # The address connect-by-MAC lent the device, while it is still there to give back.
    temp_address: str | None = None
    # How the reconnect got back in by MAC ("mac-telnet", "link-local"); None when it went
    # to an address, or when the session never had to reconnect.
    mac_method: str | None = None
    succeeded: bool = False

    @property
    def rollback_pending(self) -> bool:
        return self.rollback_at is not None


class ApplyAborted(Exception):
    def __init__(self, report: ApplyReport, issue: Issue) -> None:
        super().__init__(issue.message)
        self.report = report
        self.issue = issue


Reconnector = Callable[[ReconnectTarget], Transport | MacConnectResult]


@dataclass
class ApplyContext:
    profile: DeviceProfile
    result: GenerationResult
    options: ApplyOptions
    inventory: DeviceInventory
    transport: Transport
    uploader_factory: Callable[[Transport], FileUploader]
    reconnect: Reconnector
    session_port: str | None = None  # device interface this session comes in on
    temp_address: str | None = None  # address connect-by-MAC added, which a reset wipes
    mac_route: MacRoute | None = None  # how the session reached the device by MAC
    now: Callable[[], datetime] = datetime.now
    sleep: Callable[[float], None] = lambda seconds: None
    cancelled: Callable[[], bool] = lambda: False
    on_event: Callable[[ApplyEvent], None] = lambda event: None


class ApplyOrchestrator:
    def __init__(self, ctx: ApplyContext) -> None:
        if ctx.options.mode == ApplyMode.GENERATE_ONLY:
            raise ValueError("GENERATE_ONLY does not touch the device")
        self.ctx = ctx
        self.names = make_names(ctx.now(), ctx.inventory.has_flash_dir)
        self.report = ApplyReport(names=self.names, transport=ctx.transport)

    # ---------------------------------------------------------------- helpers

    @property
    def transport(self) -> Transport:
        return self.report.transport

    @property
    def result(self) -> GenerationResult:
        """What the device will be like afterwards, which after a reset is not what it is
        now: the services to expect and the ports to reconnect on."""
        return result_for_mode(self.ctx.result, self.ctx.options.mode)

    # Positional-only names, so message parameters like `step=` cannot collide with them.
    def _event(self, step: str, status: StepStatus, detail: str = "", /, **params: Any) -> None:
        self.ctx.on_event(ApplyEvent(step, status, params, detail))

    def _abort(self, code: str, detail: str = "", /, **params: Any) -> ApplyAborted:
        issue = Issue(code, Severity.ERROR, "", params)
        self.report.issues.append(issue)
        self._event(code, StepStatus.FAILED, detail, **params)
        return ApplyAborted(self.report, issue)

    def _warn(self, code: str, /, **params: Any) -> None:
        self.report.issues.append(Issue(code, Severity.WARNING, "", params))
        self._event(code, StepStatus.WARNING, **params)

    def _check_cancel(self) -> None:
        if self.ctx.cancelled():
            raise self._abort("apply.cancelled")

    # ---------------------------------------------------------------- flow

    def run(self) -> ApplyReport:
        # One transfer connection for both directions, released as early as possible:
        # download and upload used to open one each, and the download's stayed open for
        # the whole flow, including the reset.
        self.report.temp_address = self.ctx.temp_address
        uploader = self.ctx.uploader_factory(self.transport)
        try:
            self._check_cancel()
            self._backup()
            self._download_backups(uploader)
            self._check_cancel()
            self._upload(uploader)
            self._check_cancel()
            if self.ctx.options.mode == ApplyMode.IMPORT and self.ctx.options.rollback_minutes:
                self._schedule_rollback()
            self._check_cancel()
            # No cancellation beyond this point: the device is being changed.
            if self.ctx.options.mode == ApplyMode.IMPORT:
                self._execute_import()
            else:
                self._execute_reset()
            self._reconnect()
            self._verify()
            if self.ctx.options.mode == ApplyMode.IMPORT and self.report.upload \
                    and self.report.upload.remote_path:
                self._cleanup()
            if self.report.rollback_pending:
                self._event("apply.rollback_pending", StepStatus.WARNING,
                            at=self.report.rollback_at.strftime("%H:%M:%S"))
            self.report.succeeded = all(c.ok for c in self.report.checks)
            return self.report
        finally:
            uploader.release()  # also on the failure paths

    def _backup(self) -> None:
        name = self.names.backup_name
        self._event("apply.step.backup", StepStatus.RUNNING, name=name)
        try:
            # Encryption prompts / parameters differ per version: NUTNO OVĚŘIT.
            self.transport.execute("/system/backup/save", name=name)
            self.transport.execute("/export", file=name)
        except TransportError as exc:
            raise self._abort("apply.failed.backup", repr(exc), error=str(exc)) from exc
        self._event("apply.step.backup", StepStatus.OK, name=name)

    def _download_backups(self, uploader: FileUploader) -> None:
        target_dir = self.ctx.options.download_dir
        if target_dir is None:
            self._event("apply.step.download", StepStatus.SKIPPED, dir="-")
            return
        target_dir.mkdir(parents=True, exist_ok=True)
        for remote in (self.names.backup_file, self.names.export_file):
            local = target_dir / remote.rsplit("/", 1)[-1]
            try:
                uploader.download(remote, local)
            except UploadFailed as exc:
                self._warn("apply.warning.download", file=remote, error=str(exc))
                continue
            self.report.downloaded.append(local)
        if self.report.downloaded:
            self._event("apply.step.download", StepStatus.OK, dir=str(target_dir))

    def _upload(self, uploader: FileUploader) -> None:
        file = self.names.remote_script
        data = build_upload_script(self.result.text, self.ctx.options, self.names)
        self._event("apply.step.upload", StepStatus.RUNNING, file=file, strategy="-")
        try:
            result = uploader.upload(data.encode("utf-8"), file)
        except UploadFailed as exc:
            raise self._abort("apply.failed.upload", repr(exc), error=str(exc)) from exc
        if result.strategy == UploadStrategy.LINE_BY_LINE and \
                self.ctx.options.mode == ApplyMode.RESET_AND_IMPORT:
            raise self._abort("apply.failed.upload_reset_needs_file")
        self.report.upload = result
        uploader.release()
        self._event("apply.step.upload", StepStatus.OK, file=file, strategy=result.strategy.value)

    def _schedule_rollback(self) -> None:
        minutes = self.ctx.options.rollback_minutes or 0
        try:
            clock = self.transport.execute("/system/clock/print")[0]
            command, run_at = schedule_rollback(clock["date"], clock["time"], minutes,
                                                self.names.backup_name, ROLLBACK_JOB)
            self.transport.execute_command(command)
        except (TransportError, KeyError, ValueError) as exc:
            raise self._abort("apply.failed.rollback", repr(exc), error=str(exc)) from exc
        self.report.rollback_at = run_at
        self._event("apply.step.rollback", StepStatus.OK, at=run_at.strftime("%Y-%m-%d %H:%M:%S"))

    def _execute_import(self) -> None:
        upload = self.report.upload
        try:
            if upload is not None and upload.strategy == UploadStrategy.LINE_BY_LINE:
                items = [i for i in self.result.script.items() if not hasattr(i, "text")
                         or isinstance(i, RawLine)]
                runnable = [i for i in items if isinstance(i, Command | RawLine)]
                execute_raw = getattr(self.transport, "execute_raw", None)
                console_only = next((i for i in runnable if isinstance(i, RawLine) or (
                    isinstance(i.selector, Find) and i.selector.raw is not None)), None)
                if console_only is not None and execute_raw is None:
                    # Refused before the first line, not halfway through the configuration.
                    raise CommandError(render_inline(console_only),
                                       "raw lines need the SSH transport")
                self._event("apply.step.execute_lines", StepStatus.RUNNING, count=len(runnable))
                for item in runnable:
                    if isinstance(item, RawLine):
                        execute_raw(item)
                    else:
                        self.transport.execute_command(item)
                self._event("apply.step.execute_lines", StepStatus.OK, count=len(runnable))
            else:
                file = self.names.remote_script
                self._event("apply.step.execute_import", StepStatus.RUNNING, file=file)
                self.transport.execute("/import", file_name=file)
                self._event("apply.step.execute_import", StepStatus.OK, file=file)
        except ConnectionLost as exc:
            # Expected e.g. over SSH when the SSH port changes; verified after reconnect.
            self.report.connection_lost_during = "execute"
            self._event("apply.connection_lost", StepStatus.WARNING, repr(exc), step="import")
        except TransportError as exc:
            if self.report.rollback_pending:
                self._warn("apply.rollback_pending",
                           at=self.report.rollback_at.strftime("%H:%M:%S"))
            raise self._abort("apply.failed.execute", repr(exc), error=str(exc)) from exc

    def _arm_reset(self, file: str) -> None:
        """Arm the reset, giving the script just written time to settle first.

        RouterOS lists an SFTP-written file at once, with its final size and as a script,
        and still refuses it for run-after-reset with "cannot use file" for a moment
        afterwards. Verified on 7.20.7: the same 2011-byte script was refused 0.02 s after
        the upload and accepted 3.01 s after it, and a 2750-byte one behaved the same, so
        it is the wait that matters and not the size. A refused reset changes nothing on
        the device, which is what makes simply asking again the right answer to the jitter.
        """
        for attempt in range(1, RESET_ARM_ATTEMPTS + 1):
            self.ctx.sleep(RESET_SETTLE_S)
            try:
                # The API wants the file's .id here, the console wants its name.
                self.transport.execute("/system/reset-configuration", no_defaults=True,
                                       skip_backup=False,
                                       run_after_reset=self.transport.file_reference(file))
                return
            except CommandError as exc:
                if FILE_NOT_READY not in str(exc) or attempt == RESET_ARM_ATTEMPTS:
                    raise
                self._warn("apply.reset_retry", attempt=attempt, total=RESET_ARM_ATTEMPTS)

    def _execute_reset(self) -> None:
        file = self.names.remote_script
        self._event("apply.step.execute_reset", StepStatus.RUNNING, file=file)
        try:
            self._arm_reset(file)
        except ConnectionLost:
            pass  # the device reboots immediately; losing the session is the expected outcome
        except TransportError as exc:
            raise self._abort("apply.failed.execute", repr(exc), error=str(exc)) from exc
        self.report.connection_lost_during = "reset"
        self.report.temp_address = None  # the reset takes every address with it
        self.transport.close()
        self._event("apply.step.execute_reset", StepStatus.OK, file=file)
        self.ctx.sleep(float(self.ctx.options.reset_delay_s))

    def _reconnect(self) -> None:
        access = self.result.access
        params = self.transport.params
        expiring: tuple[str, ...] = ()
        if self.ctx.options.mode == ApplyMode.RESET_AND_IMPORT:
            params = replace(params, username="admin", password=self.ctx.options.admin_password)
            # A reset takes every address with it, including the temporary one that
            # connect-by-MAC added to reach the device - the very one we are talking to.
            expiring = (self.ctx.temp_address,) if self.ctx.temp_address else ()
        hosts = reconnect_hosts(self.ctx.profile, current_host=params.host,
                                session_port=self.ctx.session_port, expiring=expiring)
        # A device reached by MAC is reached by MAC again: after a reset it has no
        # address this PC knows of, the lent one included.
        route = self.ctx.mac_route
        target = ReconnectTarget(
            params=replace(params, port=None, host=hosts[0]),
            api_port=access.api_ssl_port, ssh_port=access.ssh_port,
            alternatives=tuple(hosts[1:]), mac=route,
            mac_interface=mac_interface_after_apply(self.ctx.profile,
                                                    route.device.interface_name)
            if route is not None else None,
        )
        if self.ctx.options.mode == ApplyMode.IMPORT and self.transport.connected:
            try:
                self.transport.execute("/system/identity/print")
                self._event("apply.step.reconnect", StepStatus.SKIPPED, hosts=params.host,
                            api_port=target.api_port, ssh_port=target.ssh_port)
                return
            except ConnectionLost:
                self.report.connection_lost_during = self.report.connection_lost_during or \
                    "verify"
        self._event("apply.step.reconnect", StepStatus.RUNNING, hosts=target.describe(),
                    api_port=target.api_port, ssh_port=target.ssh_port)
        try:
            reached = self.ctx.reconnect(target)
        except (ReconnectFailed, TransportError) as exc:
            attempts = getattr(exc, "attempts", 1)
            raise self._abort("apply.failed.reconnect", repr(exc), attempts=attempts,
                              api_port=target.api_port, ssh_port=target.ssh_port,
                              error=str(exc)) from exc
        if isinstance(reached, MacConnectResult):
            # Back in by MAC, over an address lent anew: that one is to give back now.
            self.report.transport = reached.transport
            self.report.temp_address = reached.temp_address
            self.report.mac_method = reached.method.value
            where = (f"{route.mac if route else '?'} ({reached.method.value}, "
                     f"{reached.transport.params.host})")
        else:
            self.report.transport = reached
            where = reached.params.host
        self._event("apply.step.reconnect", StepStatus.OK, hosts=where,
                    api_port=target.api_port, ssh_port=target.ssh_port)

    def _verify(self) -> None:
        self._event("apply.step.verify", StepStatus.RUNNING)
        try:
            checks, issues = verify_configuration(self.transport, self.ctx.profile, self.result)
        except TransportError as exc:
            raise self._abort("apply.failed.verify", repr(exc), error=str(exc)) from exc
        self.report.checks.extend(checks)
        self.report.issues.extend(issues)
        status = StepStatus.OK if all(c.ok for c in checks) else StepStatus.WARNING
        self._event("apply.step.verify", status)

    def _cleanup(self) -> None:
        file = self.names.remote_script
        try:
            self.transport.execute_command(cmd("/file", "remove", selector=Find.by(name=file)))
        except TransportError as exc:
            self._warn("apply.warning.cleanup", error=str(exc))
            return
        self._event("apply.step.cleanup", StepStatus.OK)

    # ---------------------------------------------------------------- after apply

    def confirm(self) -> None:
        """'Potvrdit, že konfigurace funguje': remove the rollback job."""
        if not self.report.rollback_pending:
            return
        try:
            self.transport.execute_command(cancel_rollback(ROLLBACK_JOB))
        except Cancelled:
            raise
        except TransportError as exc:
            raise self._abort("apply.failed.confirm", repr(exc), error=str(exc)) from exc
        self.report.rollback_at = None
        self._event("apply.step.confirm", StepStatus.OK)
