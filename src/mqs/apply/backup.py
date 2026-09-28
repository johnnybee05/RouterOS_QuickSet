"""The backup taken before a device is changed, shared by the apply and the version change.

`/system backup save` and `/export` are two halves of one safety net: the binary backup
restores the device as it was on the same RouterOS, and the export is the only thing left
that says what it was configured like once the version underneath has moved. A version
change needs both at least as badly as an apply does — a downgrade wipes the
configuration, and a move between v6 and v7 converts it one way and never back — so both
run this, with the same file names, the same message codes and the same copy to this PC.

The caller lends its own machinery, as it does to `apply/routerboot.py`: `event` and
`warn` put the steps in its log, and `fail` builds the exception it aborts with, so a
backup that cannot be written stops an apply as an `ApplyAborted` and a version change as
a `VersionChangeAborted`, both under `apply.failed.backup`. Nothing on the device has
changed at that point, which is what makes stopping there the right answer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from mqs.apply.events import StepStatus
from mqs.apply.plan import ApplyNames, old_backups
from mqs.generator.ir import Find, cmd
from mqs.transport.base import Transport, TransportError
from mqs.transport.upload import FileUploader, UploadFailed


@dataclass
class DeviceBackup:
    """Write the backup and the export, fetch them here, and tidy the older ones away.

    `transport` is the connection the caller is on; the steps are short and none of them
    reboots, so there is no transport to hand back afterwards.
    """

    transport: Transport
    names: ApplyNames
    event: Callable[..., None]
    warn: Callable[..., None]
    fail: Callable[..., Exception]
    # Where the copy on this PC goes; None = leave both files on the device only.
    download_dir: Path | None = None
    # Backups kept on the device, the new one included; 0 keeps them all.
    keep: int = 0
    downloaded: list[Path] = field(default_factory=list)

    def run(self, uploader: FileUploader) -> list[Path]:
        """Everything, in the one order that is safe: write, fetch, then delete old ones."""
        self.save()
        self.download(uploader)
        self.prune()
        return self.downloaded

    def save(self) -> None:
        name = self.names.backup_name
        self.event("apply.step.backup", StepStatus.RUNNING, name=name)
        try:
            # Encryption prompts / parameters differ per version: NUTNO OVĚŘIT.
            self.transport.execute("/system/backup/save", name=name)
            self.transport.execute("/export", file=name)
        except TransportError as exc:
            raise self.fail("apply.failed.backup", repr(exc), error=str(exc)) from exc
        self.event("apply.step.backup", StepStatus.OK, name=name)

    def download(self, uploader: FileUploader) -> None:
        """A file that will not come over is a warning, not a stop: it stays on the
        device, where the rollback and the next connect can still reach it."""
        target_dir = self.download_dir
        if target_dir is None:
            self.event("apply.step.download", StepStatus.SKIPPED, dir="-")
            return
        target_dir.mkdir(parents=True, exist_ok=True)
        for remote in (self.names.backup_file, self.names.export_file):
            local = target_dir / remote.rsplit("/", 1)[-1]
            try:
                uploader.download(remote, local)
            except UploadFailed as exc:
                self.warn("apply.warning.download", file=remote, error=str(exc))
                continue
            self.downloaded.append(local)
        if self.downloaded:
            self.event("apply.step.download", StepStatus.OK, dir=str(target_dir))

    def prune(self) -> None:
        """Delete all but the newest backups; every run of either flow leaves one behind."""
        if self.keep <= 0:
            return
        try:
            files = [f.get("name", "") for f in self.transport.execute("/file/print")]
            doomed = old_backups(files, self.keep, self.names.stamp)
            for name in doomed:
                self.transport.execute_command(cmd("/file", "remove",
                                                   selector=Find.by(name=name)))
        except TransportError as exc:
            self.warn("apply.warning.prune", error=str(exc))
            return
        if doomed:
            self.event("apply.step.prune", StepStatus.OK, count=len(doomed), keep=self.keep)


__all__ = ["DeviceBackup"]
