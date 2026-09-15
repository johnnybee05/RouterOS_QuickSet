"""Opt-in rollback guard (safe-mode analogue) via /system scheduler.

Anti-loop design:
- the backup is saved BEFORE the job is created, so restoring it cannot recreate the job,
- the job's script removes the job first and only then loads the backup (which reboots);
  if loading fails, the job is already gone,
- one-shot schedule computed from the DEVICE clock, never from the PC clock.

`backup load` non-interactive syntax, `interval=0` one-shot semantics and the start-date
formats are NUTNO OVĚŘIT (docs/DIALECT_NOTES.md).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from enum import StrEnum

from mqs.generator.ir import Command, Find, cmd
from mqs.generator.render import quote

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LEGACY_DATE = re.compile(r"^(?P<mon>[a-z]{3})/(?P<day>\d{2})/(?P<year>\d{4})$")
_TIME = re.compile(r"^\d{2}:\d{2}:\d{2}$")


class DateStyle(StrEnum):
    ISO = "iso"  # 2026-09-15 (newer v7)
    LEGACY = "legacy"  # sep/15/2026 (v6, older v7)


def parse_device_clock(date: str, time: str) -> tuple[datetime, DateStyle]:
    date, time = date.strip().lower(), time.strip()
    if not _TIME.match(time):
        raise ValueError(f"unrecognised device time: {time!r}")
    if _ISO_DATE.match(date):
        return datetime.fromisoformat(f"{date}T{time}"), DateStyle.ISO
    match = _LEGACY_DATE.match(date)
    if match and match["mon"] in MONTHS:
        month = MONTHS.index(match["mon"]) + 1
        parsed = datetime(int(match["year"]), month, int(match["day"]),
                          *map(int, time.split(":")))
        return parsed, DateStyle.LEGACY
    raise ValueError(f"unrecognised device date: {date!r}")


def format_device_date(value: datetime, style: DateStyle) -> str:
    if style == DateStyle.ISO:
        return value.strftime("%Y-%m-%d")
    return f"{MONTHS[value.month - 1]}/{value.day:02d}/{value.year}"


def rollback_script(job_name: str, backup_name: str) -> str:
    return (f"/system scheduler remove [find name={quote(job_name)}]; "
            f"/system backup load name={quote(backup_name)} password=\"\"")


def schedule_rollback(
    device_date: str,
    device_time: str,
    minutes: int,
    backup_name: str,
    job_name: str,
) -> tuple[Command, datetime]:
    if minutes < 1:
        raise ValueError("rollback delay must be at least one minute")
    now, style = parse_device_clock(device_date, device_time)
    run_at = now + timedelta(minutes=minutes)
    command = cmd(
        "/system scheduler",
        "add",
        name=job_name,
        start_date=format_device_date(run_at, style),
        start_time=run_at.strftime("%H:%M:%S"),
        interval="0",
        on_event=rollback_script(job_name, backup_name),
        comment="QuickSet Plus rollback - removed when the configuration is confirmed",
    )
    return command, run_at


def cancel_rollback(job_name: str) -> Command:
    return cmd("/system scheduler", "remove", selector=Find.by(name=job_name))
