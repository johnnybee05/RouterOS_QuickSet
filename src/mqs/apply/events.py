"""What a step on the device reports while it runs.

The apply and the version change both walk a device through steps, and the window and the
CLI show them the same way, so the event they hand out lives on its own here rather than
inside either flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from mqs.messages import format_message


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
