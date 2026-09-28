"""Putting a named RouterOS version on a device: downgrade, and the move between v6 and v7.

`apply/update.py` moves a device forward inside its own major version and lets the reset's
reboot do the installing. That is the only move a reboot makes by itself. Everything else
is here, and it differs in every part that matters:

* the **target is named by the user** — a downgrade is never implicit,
* an older package installs by `/system package downgrade`, which reboots by itself,
* the board's **factory version is a floor**: nothing older goes on it,
* v6 → v7 is allowed only from 6.49.22 (`V6_TO_V7_FROM`), so a device below that takes
  **two hops**,
* and the configuration is not converted downwards, so a device that goes from v7 to v6
  may come back somewhere else entirely — or nowhere this PC can reach.

The route is therefore planned first (`plan_version`) and only then walked
(`VersionChanger`), one reinstall at a time, each with its own reboot, reconnect and
version check. What the device does with a configuration in between is not relied upon:
the caller re-reads the device afterwards and generates the configuration for what came
back, which is what makes a cross-major move safe at all.

Facts this module is built on, all OVĚŘENO on the mmips hEX S on 2026-09-27 (see
docs/DIALECT_NOTES.md): `/system package downgrade` asks nothing over SSH exec, drops the
session in a second and installs the package on the reboot that follows; it **reboots even
with nothing to install**, so the package is confirmed in `/file` before it is issued; a
plain `/system reboot` installs a newer package without any reset; RouterOS deletes a
package once it has installed it; and the new RouterOS leaves its RouterBOOT firmware
waiting in `upgrade-firmware` (7.23.7 next to `current-firmware` 6.49.21 after the move to
v7), which is why the route can end with the same *Aktualizovat RouterBOOT* step an apply
ends with — see `apply/routerboot.py`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from mqs.apply.backup import DeviceBackup
from mqs.apply.events import ApplyEvent, StepStatus
from mqs.apply.packages import (
    NpkPackage,
    UpdateDir,
    extra_packages,
    main_packages,
    match_extras,
    package_arch,
    scan_update_dir,
    space_issue,
    unreadable_issues,
)
from mqs.apply.packages import (
    issue as _issue,
)
from mqs.apply.plan import DEFAULT_KEEP_BACKUPS, make_names
from mqs.apply.reconnect import ReconnectFailed, ReconnectTarget
from mqs.apply.routerboot import (
    REBOOT_WAIT_S as ROUTERBOOT_REBOOT_WAIT_S,
)
from mqs.apply.routerboot import RouterbootUpgrade, state_from_inventory
from mqs.apply.update import firmware_newer
from mqs.apply.verify import CheckResult, check_routeros_version
from mqs.generator.ir import Find, cmd
from mqs.issues import Issue, Severity
from mqs.model.inventory import DeviceInventory
from mqs.model.version import RosVersion
from mqs.transport.base import (
    Cancelled,
    CommandTimeout,
    ConnectionLost,
    Transport,
    TransportError,
)
from mqs.transport.mac_connect import MacConnectResult, MacRoute
from mqs.transport.upload import FileUploader, UploadFailed

# RouterOS 7 is reached from 6.49.22 and no earlier release: MikroTik moved the gate up to
# the last v6 release (2026-09-28), so 6.49.21 no longer crosses. Still read as a minimum
# and not as an exact version — a v6 release newer than 6.49.22, should one ever appear,
# would cross straight over instead of being sent back to 6.49.22 first.
V6_TO_V7_FROM = RosVersion(major=6, minor=49, patch=22)
# After the command that installs: how long to wait for an answer that cannot come, then
# how long to leave the device alone before reconnecting. Installing a package took the
# mmips board 84 s to come back, a bare reboot 40 s.
INSTALL_REPLY_S = 10.0
REBOOT_WAIT_S = 20.0


class Install(StrEnum):
    """How a device is made to install the packages waiting in its file root."""

    BY_REBOOT = "reboot"  # newer than the running version: any reboot installs it
    BY_DOWNGRADE = "downgrade"  # older: /system package downgrade, which reboots itself


@dataclass(frozen=True)
class Hop:
    """One reinstall: the files to upload, and how the device is made to install them."""

    packages: tuple[NpkPackage, ...]
    version: RosVersion
    install: Install
    crosses_major: bool = False

    @property
    def main(self) -> NpkPackage:
        return self.packages[0]

    @property
    def file_names(self) -> str:
        return ", ".join(p.file_name for p in self.packages)


@dataclass(frozen=True)
class VersionPlan:
    """The route from the version the device runs to the one the user asked for."""

    current: RosVersion
    target: RosVersion | None = None
    hops: tuple[Hop, ...] = ()
    floor: RosVersion | None = None  # the factory version, None = the board does not say
    issues: tuple[Issue, ...] = field(default_factory=tuple)

    @property
    def changes(self) -> bool:
        return bool(self.hops)

    @property
    def blocked(self) -> bool:
        return any(i.severity == Severity.ERROR for i in self.issues)

    @property
    def downgrades(self) -> bool:
        return any(h.install == Install.BY_DOWNGRADE for h in self.hops)

    @property
    def crosses_major(self) -> bool:
        return any(h.crosses_major for h in self.hops)

    @property
    def needs_confirmation(self) -> bool:
        """A downgrade or a move between v6 and v7 is never made on a checkbox alone."""
        return self.downgrades or self.crosses_major

    @property
    def reboots(self) -> int:
        return len(self.hops)


@dataclass(frozen=True)
class VersionOffer:
    """One version the Update directory could put on this device, for the user to pick."""

    version: RosVersion
    package: NpkPackage
    blocked_by: Issue | None = None
    current: bool = False

    @property
    def available(self) -> bool:
        return self.blocked_by is None and not self.current


def available_versions(inventory: DeviceInventory, found: UpdateDir) -> list[VersionOffer]:
    """Every RouterOS version in ``found`` built for this device, newest first.

    A version the device cannot have keeps its place in the list with the reason in
    ``blocked_by``: the window greys it out and says why, instead of hiding it. That is
    the whole point on a board like the arm hEX S, whose factory firmware is 7.20.7 —
    RouterOS 6 is in the directory, and the honest answer is "not on this board".
    """
    arch = package_arch(inventory)
    if arch is None:
        return []
    offers: list[VersionOffer] = []
    for package in main_packages(found, arch):
        plan = plan_version(inventory, found, package.version)
        blocking = next((i for i in plan.issues if i.severity == Severity.ERROR), None)
        offers.append(VersionOffer(package.version, package, blocking,
                                   current=package.version == inventory.version))
    return offers


def plan_version(inventory: DeviceInventory, found: UpdateDir,
                 target: RosVersion) -> VersionPlan:
    """The route to ``target``, or the reason there is none.

    Refuses rather than guesses: an unknown architecture, a missing package, a target
    below the board's floor or one that would need a 6.49.22 the directory does not hold
    all come back as errors, before anything is uploaded and long before a reboot.
    """
    current = inventory.version
    floor = inventory.version_floor
    notes = list(unreadable_issues(found, "version"))

    def stop(code: str, severity: Severity = Severity.ERROR, **params: object) -> VersionPlan:
        return VersionPlan(current, target, (), floor,
                           (*notes, _issue(code, severity, **params)))

    if not found.exists:
        return stop("version.no_dir", dir=str(found.directory))
    device_arch = inventory.resource.architecture
    arch = package_arch(inventory)
    if arch is None:
        return stop("version.unknown_arch", arch=device_arch or "?")
    # The version it already runs needs no file: asking for it is not an error even when
    # the directory has no package for it, and "nothing to install" is the whole answer.
    if target == current:
        return stop("version.same", Severity.INFO, version=str(current))
    mains = main_packages(found, arch)
    wanted = next((p for p in mains if p.version == target), None)
    if wanted is None:
        return stop("version.no_package", version=str(target), arch=device_arch or "?",
                    dir=str(found.directory))

    going_back = target < current
    if going_back and floor is not None and target < floor:
        return stop("version.below_factory", version=str(target), floor=str(floor))
    if going_back and floor is None:
        notes.append(_issue("version.no_factory_firmware", Severity.WARNING,
                            version=str(target)))

    # The route. Only the move up to 6.49.22 ever adds a hop of its own.
    hops: list[Hop] = []
    if current.major == 6 and target.major == 7 and current < V6_TO_V7_FROM:
        gate = next((p for p in mains if p.version.major == 6
                     and p.version >= V6_TO_V7_FROM), None)
        if gate is None:
            return stop("version.v7_needs_649", version=str(target), current=str(current),
                        gate=str(V6_TO_V7_FROM), dir=str(found.directory))
        hops.append(_hop(inventory, found, arch, gate, current, notes))
    if target.major != current.major:
        notes.append(_issue("version.crosses_major", Severity.WARNING, version=str(target),
                            current=str(current)))
        dropped = [p.name for p in extra_packages(inventory)]
        if dropped:
            notes.append(_issue("version.extras_dropped", Severity.WARNING,
                                names=", ".join(sorted(dropped)), version=str(target)))
    if target.major == 7 and current.major == 6:
        notes.append(_issue("version.v7_config_converted", Severity.WARNING,
                            version=str(target)))
    if target.major == 6 and current.major == 7:
        notes.append(_issue("version.v6_config_lost", Severity.WARNING, version=str(target)))
    hops.append(_hop(inventory, found, arch, wanted,
                     hops[-1].version if hops else current, notes,
                     crosses_major=target.major != current.major))

    # Each hop is uploaded after the one before it has rebooted, so they never have to
    # fit at the same time.
    for hop in hops:
        no_room = space_issue(inventory, hop.packages, "version", hop.version,
                              Severity.ERROR)
        if no_room is not None:
            return VersionPlan(current, target, (), floor, (*notes, no_room))

    # RouterBOOT comes with RouterOS: going up leaves a newer firmware waiting, going down
    # leaves an older one, which the step then skips of its own accord.
    board = state_from_inventory(inventory)
    if board.is_routerboard and target > current:
        notes.append(_issue("version.routerboot_after", Severity.INFO, version=str(target),
                            current=board.current or "?"))
    elif board.is_routerboard and firmware_newer(board.current, str(target)):
        # The firmware the target carries is behind the one running; saying so is better
        # than a silent "RouterBOOT zůstává" at the end of the run.
        notes.append(_issue("version.routerboot_stays", Severity.INFO, version=str(target),
                            current=board.current))
    planned = _issue("version.planned", Severity.INFO, current=str(current),
                     version=str(target), reboots=len(hops),
                     files=", ".join(h.file_names for h in hops))
    return VersionPlan(current, target, tuple(hops), floor, (*notes, planned))


def _hop(inventory: DeviceInventory, found: UpdateDir, arch: str, main: NpkPackage,
         from_version: RosVersion, notes: list[Issue], *, crosses_major: bool = False) -> Hop:
    """One reinstall of ``main``, with the device's extra packages in the same version.

    Across a major version nothing is asked for: a v7 `wifi-qcom` has no v6 counterpart
    and v6 brings its extras inside the bundle, so the packages that do not come along
    are reported once (`version.extras_dropped`) and no file is looked for.
    """
    packages = [main]
    if not crosses_major:
        extras, absent = match_extras(inventory, found, arch, main.version)
        packages += extras
        notes.extend(_issue("version.extra_missing", Severity.WARNING, name=p.name,
                            installed=p.version or "?", version=str(main.version),
                            arch=inventory.resource.architecture or "?") for p in absent)
    install = Install.BY_REBOOT if main.version > from_version else Install.BY_DOWNGRADE
    return Hop(tuple(packages), main.version, install, crosses_major)


def plan_from_dir(directory: Path, inventory: DeviceInventory,
                  target: RosVersion) -> VersionPlan:
    return plan_version(inventory, scan_update_dir(directory), target)


def offers_from_dir(directory: Path, inventory: DeviceInventory) -> list[VersionOffer]:
    return available_versions(inventory, scan_update_dir(directory))


# ------------------------------------------------------------------ walking the route


@dataclass
class VersionContext:
    """Everything walking the route needs; the network side is all injected."""

    plan: VersionPlan
    inventory: DeviceInventory
    transport: Transport
    uploader_factory: Callable[[Transport], FileUploader]
    reconnect: Callable[[ReconnectTarget], Transport | MacConnectResult]
    on_event: Callable[[ApplyEvent], None] = lambda event: None
    sleep: Callable[[float], None] = time.sleep
    cancelled: Callable[[], bool] = lambda: False
    # The backup before the first hop: `now` names its files the way an apply names its
    # own, `download_dir` is where the copy on this PC goes (None = device only), and
    # `keep_backups` is how many stay on the device, the new one included (0 = all).
    now: Callable[[], datetime] = datetime.now
    download_dir: Path | None = None
    keep_backups: int = DEFAULT_KEEP_BACKUPS
    # Where to look for the device once it comes back. The address it answers on is tried
    # first; a crossing downgrade may leave it somewhere else, so the caller adds what it
    # knows (the profile's addresses) and the MAC route, which needs no address at all.
    alternatives: tuple[str, ...] = ()
    mac_route: MacRoute | None = None
    api_port: int = 8729
    ssh_port: int = 22
    reboot_wait_s: float = REBOOT_WAIT_S
    reconnect_timeout_s: float = 300.0
    # "Aktualizovat RouterBOOT", the same step the apply ends with: the new RouterOS leaves
    # its firmware in `upgrade-firmware`, and this writes it after the last hop.
    upgrade_routerboot: bool = False
    # ... and let it go down to the firmware an older RouterOS carries, on its own switch.
    routerboot_downgrade: bool = False
    routerboot_wait_s: float = ROUTERBOOT_REBOOT_WAIT_S


@dataclass
class VersionReport:
    transport: Transport
    installed: list[RosVersion] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    # The backup and the export fetched to this PC before the first hop.
    downloaded: list[Path] = field(default_factory=list)
    temp_address: str | None = None
    mac_method: str | None = None
    succeeded: bool = False


class VersionChangeAborted(Exception):
    """The route stopped part way; ``report`` says how far it got."""

    def __init__(self, report: VersionReport, issue: Issue) -> None:
        super().__init__(issue.message)
        self.report = report
        self.issue = issue


class VersionChanger:
    """One backup, then upload, install, reconnect and check, once per hop.

    Nothing is folded into anything else: each hop is one reboot of its own, and the
    version read afterwards is what says it worked. A hop that comes back on the old
    version is a full stop — the package is taken off the device and the caller is told,
    rather than carrying on to a reset that would run on a version nobody chose.
    """

    def __init__(self, ctx: VersionContext) -> None:
        self.ctx = ctx
        self.report = VersionReport(transport=ctx.transport)

    # ---------------------------------------------------------------- helpers

    @property
    def transport(self) -> Transport:
        return self.report.transport

    def _event(self, step: str, status: StepStatus, detail: str = "", /,
               **params: object) -> None:
        self.ctx.on_event(ApplyEvent(step, status, dict(params), detail))

    def _abort(self, code: str, detail: str = "", /, **params: object) -> VersionChangeAborted:
        issue = Issue(code, Severity.ERROR, "", dict(params))
        self.report.issues.append(issue)
        self._event(code, StepStatus.FAILED, detail, **params)
        return VersionChangeAborted(self.report, issue)

    def _warn(self, code: str, /, **params: object) -> None:
        self.report.issues.append(Issue(code, Severity.WARNING, "", dict(params)))
        self._event(code, StepStatus.WARNING, **params)

    def _check_cancel(self) -> None:
        if self.ctx.cancelled():
            raise self._abort("apply.cancelled")

    # ---------------------------------------------------------------- flow

    def run(self) -> VersionReport:
        """The backup, every hop, then the firmware. A route with no hops is allowed: it
        is how the RouterBOOT step alone is run on a device already on the version it
        should have."""
        self._backup()
        for hop in self.ctx.plan.hops:
            self._check_cancel()
            uploader = self.ctx.uploader_factory(self.transport)
            try:
                self._upload(hop, uploader)
            finally:
                uploader.release()
            self._confirm_uploaded(hop)
            self._install(hop)
            self._reconnect()
            self._verify(hop)
        self._routerboot()
        self.report.succeeded = all(c.ok for c in self.report.checks)
        return self.report

    def _backup(self) -> None:
        """`/system backup save` and `/export`, before the first package is uploaded.

        The same step an apply starts with (`apply/backup.py`), and this flow needs it at
        least as much: a downgrade drops the configuration, and a move between v6 and v7
        converts it one way only. The .backup file restores on the RouterOS that wrote it
        and no other, so after a move across the major version the readable export is all
        that is left — which is why both are written, and both are fetched here.

        A route with no hops installs nothing (the RouterBOOT step run on its own), and
        leaves the configuration exactly where it was: nothing to back up against.
        """
        if not self.ctx.plan.hops:
            return
        self._check_cancel()
        step = DeviceBackup(
            transport=self.transport,
            names=make_names(self.ctx.now(), self.ctx.inventory.has_flash_dir),
            event=self._event, warn=self._warn, fail=self._abort,
            download_dir=self.ctx.download_dir, keep=self.ctx.keep_backups,
            downloaded=self.report.downloaded,
        )
        uploader = self.ctx.uploader_factory(self.transport)
        try:
            step.run(uploader)
        finally:
            uploader.release()

    def _routerboot(self) -> None:
        """"Aktualizovat RouterBOOT", once, after the last hop.

        Not between hops: the firmware of an in-between version is of no interest, and a
        reboot per hop is enough. The shared step decides whether there is anything to
        write, so a downgrade ends with `apply.step.routerboot_current` and no reboot.
        """
        if not self.ctx.upgrade_routerboot:
            return
        self._check_cancel()
        step = RouterbootUpgrade(
            transport=self.transport, event=self._event, warn=self._warn,
            reconnect=self._reconnect_returning, checks=self.report.checks,
            sleep=self.ctx.sleep, wait_s=self.ctx.routerboot_wait_s,
            allow_older=self.ctx.routerboot_downgrade,
        )
        try:
            self.report.transport = step.run()
        except TransportError as exc:
            raise self._abort("apply.failed.verify", repr(exc), error=str(exc)) from exc

    def _reconnect_returning(self) -> Transport:
        self._reconnect()
        return self.transport

    def _upload(self, hop: Hop, uploader: FileUploader) -> None:
        """The packages go to the file root, where a reboot installs them from.

        Always the root, even on a board whose scripts go to `flash/`: there the root is
        a RAM disk and the package is held in memory until the reboot writes it.
        """
        for package in hop.packages:
            file = package.file_name
            size = f"{package.size / 1_000_000:.1f}"
            self._event("apply.step.version_upload", StepStatus.RUNNING, file=file, size=size,
                        strategy="-")
            try:
                result = uploader.upload(package.path.read_bytes(), file)
            except (OSError, UploadFailed) as exc:
                self._withdraw(hop)
                raise self._abort("apply.failed.version_upload", repr(exc), file=file,
                                  error=str(exc)) from exc
            self._event("apply.step.version_upload", StepStatus.OK, file=file, size=size,
                        strategy=result.strategy.value)

    def _confirm_uploaded(self, hop: Hop) -> None:
        """Every file of the hop is on the device before it is told to install.

        `/system package downgrade` reboots whether or not there is anything to install
        (OVĚŘENO 2026-09-27), so a lost upload would cost a reboot and change nothing.
        """
        try:
            on_device = {f.get("name", "") for f in self.transport.execute("/file/print")}
        except TransportError as exc:
            raise self._abort("apply.failed.version_install", repr(exc),
                              error=str(exc)) from exc
        missing = [p.file_name for p in hop.packages if p.file_name not in on_device]
        if missing:
            self._withdraw(hop)
            raise self._abort("apply.failed.version_missing_file",
                              files=", ".join(missing))

    def _install(self, hop: Hop) -> None:
        """Tell the device to install what is waiting for it; it reboots either way."""
        step = ("apply.step.version_install_reboot" if hop.install == Install.BY_REBOOT
                else "apply.step.version_install_downgrade")
        command = ("/system/reboot" if hop.install == Install.BY_REBOOT
                   else "/system/package/downgrade")
        self._event(step, StepStatus.RUNNING, version=str(hop.version),
                    current=str(self.ctx.plan.current))
        try:
            with self.transport.reply_timeout(INSTALL_REPLY_S):
                self.transport.execute(command)
        except (ConnectionLost, CommandTimeout):
            pass  # the device goes down with the session; that is the expected answer
        except Cancelled:
            raise
        except TransportError as exc:
            self._withdraw(hop)
            raise self._abort("apply.failed.version_install", repr(exc),
                              error=str(exc)) from exc
        self.transport.close()
        self._event(step, StepStatus.OK, version=str(hop.version),
                    current=str(self.ctx.plan.current))
        self.ctx.sleep(self.ctx.reboot_wait_s)

    def _reconnect(self) -> None:
        ctx = self.ctx
        target = ReconnectTarget(
            params=replace(self.transport.params, port=None),
            api_port=ctx.api_port, ssh_port=ctx.ssh_port,
            alternatives=tuple(h for h in ctx.alternatives
                               if h and h != self.transport.params.host),
            mac=ctx.mac_route,
        )
        self._event("apply.step.reconnect", StepStatus.RUNNING, hosts=target.describe(),
                    api_port=target.api_port, ssh_port=target.ssh_port)
        try:
            reached = ctx.reconnect(target)
        except (ReconnectFailed, TransportError) as exc:
            raise self._abort("apply.failed.version_reconnect", repr(exc),
                              attempts=getattr(exc, "attempts", 1), api_port=target.api_port,
                              ssh_port=target.ssh_port, error=str(exc)) from exc
        if isinstance(reached, MacConnectResult):
            self.report.transport = reached.transport
            self.report.temp_address = reached.temp_address
            self.report.mac_method = reached.method.value
            where = f"{ctx.mac_route.mac if ctx.mac_route else '?'} ({reached.method.value})"
        else:
            self.report.transport = reached
            where = reached.params.host
        self._event("apply.step.reconnect", StepStatus.OK, hosts=where,
                    api_port=target.api_port, ssh_port=target.ssh_port)

    def _verify(self, hop: Hop) -> None:
        """The device runs what the hop installed, and carries no package it did not."""
        try:
            check = check_routeros_version(self.transport, hop.version)
            left = [f.get("name", "") for f in self.transport.execute("/file/print")]
        except TransportError as exc:
            raise self._abort("apply.failed.version_install", repr(exc),
                              error=str(exc)) from exc
        self.report.checks.append(check)
        leftover = sorted({p.file_name for p in hop.packages} & set(left))
        if leftover:
            self._remove(leftover)
            self._warn("apply.warning.version_leftover", files=", ".join(leftover))
        if not check.ok:
            raise self._abort("apply.failed.version_not_installed",
                              expected=str(hop.version),
                              actual=check.params.get("actual", "?"))
        self.report.installed.append(hop.version)
        self._event("apply.step.version_installed", StepStatus.OK, version=str(hop.version))

    def _withdraw(self, hop: Hop) -> None:
        """Take a hop's packages back off a device that is not going to install them."""
        self._remove([p.file_name for p in hop.packages])

    def _remove(self, files: Sequence[str]) -> None:
        if not files or not self.transport.connected:
            return
        try:
            for name in files:
                self.transport.execute_command(cmd("/file", "remove",
                                                   selector=Find.by(name=name)))
        except TransportError as exc:
            self._warn("apply.warning.version_left", files=", ".join(files), error=str(exc))
            return
        self._event("apply.step.version_withdrawn", StepStatus.OK, files=", ".join(files))
