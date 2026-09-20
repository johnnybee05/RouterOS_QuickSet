"""Apply modes, options, file names and the uploaded script (spec 4.6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from mqs.generator.blocks.common import MASKED_SECRET
from mqs.generator.ir import Find, RawLine, cmd
from mqs.generator.render import render_inline

SCRIPT_PREFIX = "quickset"
BACKUP_PREFIX = "pre-quickset"
ROLLBACK_JOB = "mqs-rollback"
# Interfaces may not be up yet when run-after-reset starts; NUTNO OVĚŘIT the default.
DEFAULT_RESET_DELAY_S = 15
MIN_ROLLBACK_MINUTES = 2


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
    download_dir: Path | None = None  # where to store the pre-apply backup and export
    import_timeout_s: float = 300.0
    reconnect_timeout_s: float = 300.0
    confirm_dialect_mismatch: bool = False
    confirmed_identity: str = ""  # RESET: identity typed by the user in the confirm dialog


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
    def boot_file(self) -> str:
        return f"{SCRIPT_PREFIX}-{self.stamp}-boot.rsc"

    @property
    def remote_boot_script(self) -> str:
        """The file handed to `run-after-reset`; it imports `remote_script`."""
        return self.remote_dir + self.boot_file

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
    RESET wraps it: admin password and self-removal of the file, because after the reset
    nobody else is there to clean up (decision Q2 / Q10). The start delay lives in the
    bootstrap, which is what actually runs after the reset.
    """
    if options.mode != ApplyMode.RESET_AND_IMPORT:
        return generated_text
    lines = [
        "# QuickSet Plus configuration, imported by the bootstrap after a reset",
        generated_text.rstrip("\n"),
        "# ---- ADMIN PASSWORD / CLEANUP ----",
    ]
    if options.admin_password:
        password = MASKED_SECRET if mask_secrets else options.admin_password
        lines.append(render_inline(cmd("/user", "set", selector=Find.by(name="admin"),
                                       password=password)))
    lines.append(render_inline(cmd("/file", "remove", selector=Find.by(name=names.remote_script))))
    return "\n".join(lines) + "\n"


def build_preview(generated_text: str, options: ApplyOptions, names: ApplyNames) -> str:
    """What the dry-run shows: every file that will run, in the order the device runs it."""
    script = build_upload_script(generated_text, options, names, mask_secrets=True)
    if options.mode != ApplyMode.RESET_AND_IMPORT:
        return script
    return (f"# ==== {names.boot_file} — run-after-reset ====\n"
            f"{build_bootstrap_script(options, names)}\n"
            f"# ==== {names.script_file} — imported by the bootstrap ====\n{script}")


def build_bootstrap_script(options: ApplyOptions, names: ApplyNames) -> str:
    """The few lines actually given to `run-after-reset`.

    RouterOS refuses a larger script there with "cannot use file": measured on 7.20.7 on
    2026-09-20, a script accepted at 2131 bytes is refused at 2150, whatever it is named
    and however the bytes are distributed over lines. Generated configurations are around
    2.7 kB, so the configuration is imported from a second file instead, which nothing
    but `/import` ever reads and which therefore has no size limit.

    The device cleans up after itself only when the import succeeded: a failed `/import`
    stops the script, so both files stay behind for diagnosis.
    """
    return "\n".join([
        "# QuickSet Plus bootstrap: run-after-reset cannot take the configuration itself",
        render_inline(RawLine(f":delay {options.reset_delay_s}s")),
        render_inline(RawLine(f"/import file-name={names.remote_script}")),
        render_inline(cmd("/file", "remove", selector=Find.by(name=names.remote_boot_script))),
    ]) + "\n"
