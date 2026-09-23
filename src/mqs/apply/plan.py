"""Apply modes, options, file names and the uploaded script (spec 4.6)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from mqs.generator.base import GenerationResult
from mqs.generator.blocks.common import MASKED_SECRET
from mqs.generator.hardening.access import analyze_access
from mqs.generator.ir import Find, RawLine, SectionKey, cmd
from mqs.generator.render import render_inline

SCRIPT_PREFIX = "quickset"
BACKUP_PREFIX = "pre-quickset"
ROLLBACK_JOB = "mqs-rollback"
# Interfaces may not be up yet when run-after-reset starts; NUTNO OVĚŘIT the default.
DEFAULT_RESET_DELAY_S = 15
MIN_ROLLBACK_MINUTES = 2
DEFAULT_KEEP_BACKUPS = 3
# pre-quickset-20260921-234537.backup / .rsc, optionally under flash/
BACKUP_FILE_RE = re.compile(
    rf"^(?:flash/)?{BACKUP_PREFIX}-(?P<stamp>\d{{8}}-\d{{6}})\.(?:backup|rsc)$")


class ApplyMode(StrEnum):
    GENERATE_ONLY = "generate"
    IMPORT = "import"
    RESET_AND_IMPORT = "reset"


@dataclass(frozen=True)
class ApplyOptions:
    mode: ApplyMode
    rollback_minutes: int | None = None  # opt-in safety net, IMPORT only
    reset_delay_s: int = DEFAULT_RESET_DELAY_S
    admin_password: str = field(default="", repr=False)  # RESET: new admin password
    # RESET: `keep-users=yes`, the users and their passwords survive the reset, so the
    # admin password becomes optional and the session's own account gets back in.
    keep_users: bool = False
    download_dir: Path | None = None  # where to store the pre-apply backup and export
    import_timeout_s: float = 300.0
    reconnect_timeout_s: float = 300.0
    confirm_dialect_mismatch: bool = False
    confirmed_identity: str = ""  # RESET: identity typed by the user in the confirm dialog
    # Pre-apply backups kept on the device, the new one included; 0 keeps them all.
    keep_backups: int = DEFAULT_KEEP_BACKUPS


@dataclass(frozen=True)
class ApplyNames:
    stamp: str
    remote_dir: str  # "" or "flash/"

    @property
    def script_file(self) -> str:
        return f"{SCRIPT_PREFIX}-{self.stamp}.rsc"

    @property
    def remote_script(self) -> str:
        return self.remote_dir + self.script_file

    @property
    def backup_name(self) -> str:
        """Used for both `/system backup save name=` and `/export file=`."""
        return self.remote_dir + f"{BACKUP_PREFIX}-{self.stamp}"

    @property
    def backup_file(self) -> str:
        return self.backup_name + ".backup"

    @property
    def export_file(self) -> str:
        return self.backup_name + ".rsc"


def old_backups(file_names: Iterable[str], keep: int, current_stamp: str) -> list[str]:
    """Pre-apply backup files to delete so that only the ``keep`` newest remain.

    Age comes from the stamp in the name, not from the file date: the hEX S clock starts
    at 2026-01-07 after every reset until NTP answers, so files written then carry that
    date. A backup counts as its .backup and .rsc together. The current one is never
    deleted, a rollback job may be waiting to load it; ``keep`` 0 deletes nothing.
    """
    if keep <= 0:
        return []
    by_stamp: dict[str, list[str]] = {}
    for name in file_names:
        match = BACKUP_FILE_RE.match(name)
        if match:
            by_stamp.setdefault(match.group("stamp"), []).append(name)
    kept = set(sorted(by_stamp, reverse=True)[:keep]) | {current_stamp}
    return sorted(name for stamp, names in by_stamp.items() if stamp not in kept
                  for name in names)


def result_for_mode(result: GenerationResult, mode: ApplyMode) -> GenerationResult:
    """``result`` with /ip service judged against the state ``mode`` starts from.

    The generator measures the script against the services the device has now, which is
    what an import changes. A reset puts them back to factory defaults first, so what the
    device has now tells nothing about the result: on 2026-09-21 an ssh switched off
    before a reset was "expected" off afterwards, and the checklist called a device that
    was exactly right wrong. The same baseline gives the ports to reconnect on.
    """
    if mode != ApplyMode.RESET_AND_IMPORT:
        return result
    access = analyze_access(result.script.section(SectionKey.SERVICES).commands())
    issues = tuple(i for i in result.issues if i not in result.access.issues)
    return replace(result, access=access, issues=issues + access.issues)


def make_names(now: datetime, has_flash_dir: bool) -> ApplyNames:
    # Files in the RAM root do not survive a reboot on devices with a flash directory.
    return ApplyNames(stamp=now.strftime("%Y%m%d-%H%M%S"),
                      remote_dir="flash/" if has_flash_dir else "")


def build_upload_script(
    generated_text: str,
    options: ApplyOptions,
    names: ApplyNames,
    *,
    mask_secrets: bool = False,
) -> str:
    """Script that is uploaded to the device.

    IMPORT uploads the generated script unchanged (removed by the apply flow afterwards).
    RESET wraps it: start delay, admin password, and self-removal of the file, because
    after the reset nobody else is there to clean up (decision Q2 / Q10).
    """
    if options.mode != ApplyMode.RESET_AND_IMPORT:
        return generated_text
    lines = [
        "# QuickSet Plus run-after-reset script",
        render_inline(RawLine(f":delay {options.reset_delay_s}s")),
        generated_text.rstrip("\n"),
        "# ---- ADMIN PASSWORD / CLEANUP ----",
    ]
    if options.admin_password:
        password = MASKED_SECRET if mask_secrets else options.admin_password
        lines.append(render_inline(cmd("/user", "set", selector=Find.by(name="admin"),
                                       password=password)))
    lines.append(render_inline(cmd("/file", "remove", selector=Find.by(name=names.remote_script))))
    return "\n".join(lines) + "\n"
