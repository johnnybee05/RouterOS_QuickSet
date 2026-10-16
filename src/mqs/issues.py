"""Validation / generation issues shared by all layers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import StrEnum
from typing import Any

from mqs.messages import format_message


class Severity(StrEnum):
    ERROR = "error"  # blocks applying the configuration
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True, eq=True)
class Issue:
    code: str
    severity: Severity = Severity.ERROR
    field: str = ""  # dotted model path, e.g. "wan.interface"
    params: dict[str, Any] = dataclass_field(default_factory=dict, hash=False, compare=True)

    @property
    def message(self) -> str:
        return format_message(self.code, self.params)

    def __str__(self) -> str:
        where = f" [{self.field}]" if self.field else ""
        return f"{self.severity.value}{where}: {self.message}"


def has_errors(issues: Iterable[Issue]) -> bool:
    return any(i.severity == Severity.ERROR for i in issues)


def codes(issues: Iterable[Issue]) -> set[str]:
    return {i.code for i in issues}
