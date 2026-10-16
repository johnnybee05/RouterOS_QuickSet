"""Issue messages in the language in use (mqs.i18n). The catalogs are mqs.messages_cs, the
source, and mqs.messages_en; a code missing from a translation falls back to Czech."""

from __future__ import annotations

from collections.abc import Mapping
from types import ModuleType
from typing import Any

from mqs import i18n, messages_cs, messages_en

_CATALOGS: dict[str, ModuleType] = {"cs": messages_cs, "en": messages_en}


def catalog() -> ModuleType:
    return _CATALOGS.get(i18n.language(), messages_cs)


def format_message(code: str, params: Mapping[str, Any] | None = None) -> str:
    template = catalog().MESSAGES.get(code) or messages_cs.MESSAGES.get(code)
    if template is None:
        return code
    try:
        return template.format(**(params or {}))
    except (KeyError, IndexError):
        return template


def step_prefix(status: str) -> str:
    """The label of one apply step, by StepStatus value: the window and the CLI alike."""
    return catalog().STEP_PREFIX.get(status) or messages_cs.STEP_PREFIX[status]


def device_note(note: str) -> str:
    """A note from data/known_devices.yaml (written in Czech) in the language in use."""
    phrases = getattr(catalog(), "NOTE_PHRASES", ())
    for czech, translated in phrases:
        note = note.replace(czech, translated)
    return note
